from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db import DatabaseError, connection, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .models import (
    AccountActivity,
    DeveloperAuditLog,
    GoogleIdentity,
    UserPreference,
    Website,
    WebsiteAlert,
    WebsiteChange,
)


User = get_user_model()


class DeveloperUserUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("first_name", "last_name")


class DeveloperWebsiteUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Website
        fields = ("name", "url", "element_selector", "status", "check_interval")

    def validate_check_interval(self, value):
        if not 1 <= value <= 1440:
            raise serializers.ValidationError("Choose an interval from 1 to 1440 minutes.")
        return value


class DeveloperPreferenceUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPreference
        fields = ("email_alerts_enabled", "theme")


class DeveloperAlertUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebsiteAlert
        fields = ("is_read",)

    def update(self, instance, validated_data):
        is_read = validated_data.get("is_read", instance.is_read)
        if is_read != instance.is_read:
            instance.is_read = is_read
            instance.read_at = timezone.now() if is_read else None
        instance.save()
        return instance


def _field(key, label, data_type="text", *, sort=None, editable=False, options=None):
    result = {
        "key": key,
        "label": label,
        "type": data_type,
        "sortable": sort is not None,
        "editable": editable,
    }
    if options is not None:
        result["options"] = [{"value": value, "label": label} for value, label in options]
    return result


TABLES = {
    "users": {
        "model": User,
        "label": "Users",
        "fields": [
            _field("id", "ID", "integer", sort="pk"),
            _field("username", "Username", sort="username"),
            _field("email", "Email", "email", sort="email"),
            _field("first_name", "First name", editable=True),
            _field("last_name", "Last name", editable=True),
            _field("is_active", "Active", "boolean", sort="is_active"),
            _field("is_staff", "Staff", "boolean", sort="is_staff"),
            _field("date_joined", "Joined", "datetime", sort="date_joined"),
        ],
        "search": ("username__icontains", "email__icontains", "first_name__icontains", "last_name__icontains"),
        "sort": {"id": "pk", "username": "username", "email": "email", "is_active": "is_active", "is_staff": "is_staff", "date_joined": "date_joined"},
        "filters": {
            "is_active": {"label": "Account status", "field": "is_active", "options": (("true", "Active"), ("false", "Inactive"))},
            "is_staff": {"label": "Developer access", "field": "is_staff", "options": (("true", "Staff"), ("false", "Regular"))},
        },
        "update": DeveloperUserUpdateSerializer,
        "delete": False,
    },
    "websites": {
        "model": Website,
        "label": "Websites",
        "fields": [
            _field("id", "ID", "integer", sort="pk"),
            _field("name", "Name", sort="name", editable=True),
            _field("url", "URL", "url", sort="url", editable=True),
            _field("owner_email", "Owner", "email", sort="owner__email"),
            _field("element_selector", "Element selector", editable=True),
            _field("status", "Status", "choice", sort="status", editable=True, options=Website.STATUS_CHOICES),
            _field("requires_login", "Requires website login", "boolean"),
            _field("check_interval", "Check interval (minutes)", "integer", sort="check_interval", editable=True),
            _field("last_checked", "Last checked", "datetime", sort="last_checked"),
            _field("last_changed", "Last changed", "datetime", sort="last_changed"),
            _field("created_at", "Created", "datetime", sort="created_at"),
        ],
        "search": ("name__icontains", "url__icontains", "owner__email__icontains"),
        "sort": {"id": "pk", "name": "name", "url": "url", "owner_email": "owner__email", "status": "status", "check_interval": "check_interval", "last_checked": "last_checked", "last_changed": "last_changed", "created_at": "created_at"},
        "filters": {
            "status": {"label": "Monitoring status", "field": "status", "options": tuple((value, label) for value, label in Website.STATUS_CHOICES)},
        },
        "update": DeveloperWebsiteUpdateSerializer,
        "delete": True,
    },
    "website-alerts": {
        "model": WebsiteAlert,
        "label": "Website alerts",
        "fields": [
            _field("id", "ID", "integer", sort="pk"),
            _field("user_email", "User", "email", sort="user__email"),
            _field("website_name", "Website", sort="change__website__name"),
            _field("change_id", "Change ID", "integer", sort="change_id"),
            _field("is_read", "Read", "boolean", sort="is_read", editable=True),
            _field("created_at", "Created", "datetime", sort="created_at"),
            _field("read_at", "Read at", "datetime", sort="read_at"),
        ],
        "search": ("user__email__icontains", "change__website__name__icontains"),
        "sort": {"id": "pk", "user_email": "user__email", "website_name": "change__website__name", "change_id": "change_id", "is_read": "is_read", "created_at": "created_at", "read_at": "read_at"},
        "filters": {
            "is_read": {"label": "Read status", "field": "is_read", "options": (("true", "Read"), ("false", "Unread"))},
        },
        "update": DeveloperAlertUpdateSerializer,
        "delete": True,
    },
    "website-changes": {
        "model": WebsiteChange,
        "label": "Website changes",
        "fields": [
            _field("id", "ID", "integer", sort="pk"),
            _field("website_name", "Website", sort="website__name"),
            _field("website_id", "Website ID", "integer", sort="website_id"),
            _field("element_selector", "Element selector"),
            _field("detected_at", "Detected", "datetime", sort="detected_at"),
            _field("notification_sent_at", "Notification sent", "datetime", sort="notification_sent_at"),
            _field("notification_attempts", "Notification attempts", "integer", sort="notification_attempts"),
            _field("notification_next_attempt_at", "Next notification attempt", "datetime", sort="notification_next_attempt_at"),
        ],
        "search": ("website__name__icontains", "element_selector__icontains"),
        "sort": {"id": "pk", "website_name": "website__name", "website_id": "website_id", "detected_at": "detected_at", "notification_sent_at": "notification_sent_at", "notification_attempts": "notification_attempts", "notification_next_attempt_at": "notification_next_attempt_at"},
        "filters": {},
        "update": None,
        "delete": True,
    },
    "activity-history": {
        "model": AccountActivity,
        "label": "Activity history",
        "fields": [
            _field("id", "ID", "integer", sort="pk"),
            _field("user_email", "User", "email", sort="user__email"),
            _field("event_type", "Event", sort="event_type"),
            _field("description", "Description"),
            _field("website_name", "Website"),
            _field("website_url", "Website URL", "url"),
            _field("created_at", "Created", "datetime", sort="created_at"),
        ],
        "search": ("user__email__icontains", "event_type__icontains", "description__icontains", "website_name__icontains", "website_url__icontains"),
        "sort": {"id": "pk", "user_email": "user__email", "event_type": "event_type", "created_at": "created_at"},
        "filters": {},
        "update": None,
        "delete": False,
    },
    "user-preferences": {
        "model": UserPreference,
        "label": "Notification settings",
        "fields": [
            _field("id", "ID", "integer", sort="pk"),
            _field("user_email", "User", "email", sort="user__email"),
            _field("email_alerts_enabled", "Email alerts enabled", "boolean", sort="email_alerts_enabled", editable=True),
            _field("theme", "Theme", "choice", sort="theme", editable=True, options=UserPreference.THEME_CHOICES),
            _field("updated_at", "Updated", "datetime", sort="updated_at"),
        ],
        "search": ("user__email__icontains",),
        "sort": {"id": "pk", "user_email": "user__email", "email_alerts_enabled": "email_alerts_enabled", "theme": "theme", "updated_at": "updated_at"},
        "filters": {
            "email_alerts_enabled": {"label": "Email alerts", "field": "email_alerts_enabled", "options": (("true", "Enabled"), ("false", "Disabled"))},
            "theme": {"label": "Theme", "field": "theme", "options": tuple((value, label) for value, label in UserPreference.THEME_CHOICES)},
        },
        "update": DeveloperPreferenceUpdateSerializer,
        "delete": False,
    },
    "google-identities": {
        "model": GoogleIdentity,
        "label": "Google connections",
        "fields": [
            _field("id", "ID", "integer", sort="pk"),
            _field("user_email", "WebWatch user", "email", sort="user__email"),
            _field("linked_at", "Linked", "datetime", sort="linked_at"),
        ],
        "search": ("user__email__icontains",),
        "sort": {"id": "pk", "user_email": "user__email", "linked_at": "linked_at"},
        "filters": {},
        "update": None,
        "delete": False,
    },
}


def _json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _read_field(record, key):
    if key == "owner_email":
        value = record.owner.email
    elif key == "user_email":
        value = record.user.email
    elif key == "website_name" and isinstance(record, WebsiteAlert):
        value = record.change.website.name
    elif key == "website_name" and isinstance(record, WebsiteChange):
        value = record.website.name
    else:
        value = getattr(record, key)
    return _json_value(value)


def _safe_record(record, config):
    return {item["key"]: _read_field(record, item["key"]) for item in config["fields"]}


def _serialize_page_size(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _get_table(model_key):
    config = TABLES.get(model_key)
    return {**config, "key": model_key} if config is not None else None


def _records_queryset(config):
    queryset = config["model"].objects.all()
    related = {
        "websites": ("owner",),
        "website-alerts": ("user", "change__website"),
        "website-changes": ("website",),
        "activity-history": ("user",),
        "user-preferences": ("user",),
        "google-identities": ("user",),
    }
    return queryset.select_related(*related.get(config["key"], ()))


def _table_metadata(key, config):
    return {
        "key": key,
        "label": config["label"],
        "count": config["model"].objects.count(),
        "fields": config["fields"],
        "filters": [
            {"key": name, "label": values["label"], "options": [
                {"value": option, "label": label} for option, label in values["options"]
            ]}
            for name, values in config["filters"].items()
        ],
        "editable": config["update"] is not None,
        "deletable": config["delete"],
    }


@api_view(["GET"])
@permission_classes([IsAdminUser])
def developer_overview(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        counts = {
            "users": User.objects.count(),
            "websites": Website.objects.count(),
            "alerts": WebsiteAlert.objects.count(),
        }
        database = {"healthy": True, "vendor": connection.vendor}
    except DatabaseError:
        counts = {"users": None, "websites": None, "alerts": None}
        database = {"healthy": False, "vendor": connection.vendor}

    return Response({
        "counts": counts,
        "database": database,
        "recent_errors_available": False,
        "recent_errors": [],
    })


@api_view(["GET"])
@permission_classes([IsAdminUser])
def developer_tables(request):
    try:
        tables = [_table_metadata(key, config) for key, config in TABLES.items()]
    except DatabaseError:
        return Response({"detail": "Database table information is unavailable."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response({"tables": tables})


@api_view(["GET"])
@permission_classes([IsAdminUser])
def developer_records(request, model_key):
    config = _get_table(model_key)
    if config is None:
        return Response({"detail": "This model is not available in the developer explorer."}, status=status.HTTP_404_NOT_FOUND)

    page = _serialize_page_size(request.query_params.get("page", "1"))
    page_size = _serialize_page_size(request.query_params.get("page_size", "25"))
    if page is None or page_size is None:
        return Response({"detail": "Page and page size must be positive integers."}, status=status.HTTP_400_BAD_REQUEST)
    if page_size > 100:
        return Response({"detail": "Page size cannot exceed 100 records."}, status=status.HTTP_400_BAD_REQUEST)

    queryset = _records_queryset({**config, "key": model_key})
    search = request.query_params.get("q", "").strip()
    if len(search) > 200:
        return Response({"detail": "Search text cannot exceed 200 characters."}, status=status.HTTP_400_BAD_REQUEST)
    if search and config["search"]:
        condition = Q()
        for field_name in config["search"]:
            condition |= Q(**{field_name: search})
        queryset = queryset.filter(condition)

    filter_name = request.query_params.get("filter", "")
    filter_value = request.query_params.get("value", "")
    if filter_name:
        filter_config = config["filters"].get(filter_name)
        allowed_values = {option for option, _label in filter_config["options"]} if filter_config else set()
        if filter_config is None or filter_value not in allowed_values:
            return Response({"detail": "This filter is not supported."}, status=status.HTTP_400_BAD_REQUEST)
        field_value = filter_value
        if filter_value in {"true", "false"}:
            field_value = filter_value == "true"
        queryset = queryset.filter(**{filter_config["field"]: field_value})

    sort_key = request.query_params.get("sort", "id")
    sort_field = config["sort"].get(sort_key)
    if sort_field is None:
        return Response({"detail": "This sort field is not supported."}, status=status.HTTP_400_BAD_REQUEST)
    direction = request.query_params.get("direction", "desc")
    if direction not in {"asc", "desc"}:
        return Response({"detail": "Sort direction must be asc or desc."}, status=status.HTTP_400_BAD_REQUEST)
    queryset = queryset.order_by(sort_field if direction == "asc" else f"-{sort_field}")

    paginator = Paginator(queryset, page_size)
    current_page = paginator.get_page(page)
    return Response({
        "model": _table_metadata(model_key, config),
        "count": paginator.count,
        "page": current_page.number,
        "page_size": page_size,
        "num_pages": paginator.num_pages,
        "results": [_safe_record(record, config) for record in current_page.object_list],
    })


def _record_for_key(config, record_id):
    try:
        primary_key = config["model"]._meta.pk.to_python(record_id)
    except (TypeError, ValueError, OverflowError):
        return None
    if primary_key is None:
        return None
    queryset = _records_queryset(config)
    return queryset.filter(pk=primary_key).first()


def _delete_description(model_key, record):
    if model_key == "websites":
        changes = WebsiteChange.objects.filter(website=record)
        change_count = changes.count()
        alert_count = WebsiteAlert.objects.filter(change__website=record).count()
        credential_count = int(Website.objects.filter(pk=record.pk, credentials__isnull=False).exists())
        consequences = ["The website record will be deleted."]
        if credential_count:
            consequences.append("Its encrypted website login credentials and browser session will also be deleted.")
        if change_count:
            consequences.append(f"{change_count} change-history record(s) will also be deleted.")
        if alert_count:
            consequences.append(f"{alert_count} linked alert(s) will also be deleted.")
        consequences.append("Queued checks are not removed from Celery or Redis; a later check for this deleted website will safely fail without affecting other jobs.")
        return consequences
    if model_key == "website-changes":
        alert_count = record.alerts.count()
        return [
            "This change-history record will be deleted.",
            *([f"{alert_count} linked alert(s) will also be deleted."] if alert_count else []),
        ]
    return ["Only this alert record will be deleted."]


def _record_detail_payload(model_key, config, record):
    editable_fields = [item["key"] for item in config["fields"] if item["editable"]]
    return {
        "model": _table_metadata(model_key, config),
        "record": _safe_record(record, config),
        "editable_fields": editable_fields,
        "deletion": {
            "allowed": config["delete"],
            "confirmation_required": config["delete"],
            "consequences": _delete_description(model_key, record) if config["delete"] else [],
        },
    }


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAdminUser])
def developer_record_detail(request, model_key, record_id):
    config = _get_table(model_key)
    if config is None:
        return Response({"detail": "This model is not available in the developer explorer."}, status=status.HTTP_404_NOT_FOUND)
    record = _record_for_key(config, record_id)
    if record is None:
        return Response({"detail": "Record not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(_record_detail_payload(model_key, config, record))

    if request.method == "PATCH":
        serializer_class = config["update"]
        if serializer_class is None:
            return Response({"detail": "This model is read-only in the developer console."}, status=status.HTTP_405_METHOD_NOT_ALLOWED)
        if not isinstance(request.data, dict):
            return Response({"detail": "Submit an object containing editable fields."}, status=status.HTTP_400_BAD_REQUEST)
        unsupported_fields = set(request.data.keys()) - set(serializer_class.Meta.fields)
        if unsupported_fields:
            return Response({"detail": "One or more submitted fields cannot be edited."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            with transaction.atomic():
                locked_record = config["model"].objects.select_for_update().get(pk=record.pk)
                serializer = serializer_class(locked_record, data=request.data, partial=True)
                serializer.is_valid(raise_exception=True)
                changed_fields = [
                    field_name
                    for field_name, value in serializer.validated_data.items()
                    if getattr(locked_record, field_name) != value
                ]
                if changed_fields:
                    serializer.save()
                    if model_key == "website-alerts" and "is_read" in changed_fields:
                        changed_fields.append("read_at")
                    DeveloperAuditLog.objects.create(
                        actor=request.user,
                        action="update",
                        model_key=model_key,
                        record_id=str(locked_record.pk),
                        changed_fields=sorted(set(changed_fields)),
                    )
                updated_record = _record_for_key(config, locked_record.pk)
        except serializers.ValidationError as error:
            return Response(error.detail, status=status.HTTP_400_BAD_REQUEST)
        except DatabaseError:
            return Response({"detail": "The update could not be saved because of a database constraint."}, status=status.HTTP_409_CONFLICT)
        return Response(_record_detail_payload(model_key, config, updated_record))

    if not config["delete"]:
        return Response({"detail": "Deletion is disabled for this model."}, status=status.HTTP_405_METHOD_NOT_ALLOWED)
    if (
        not isinstance(request.data, dict)
        or request.data.get("confirm") is not True
        or request.data.get("backup_confirmed") is not True
        or str(request.data.get("record_id", "")) != str(record.pk)
    ):
        return Response(
            {"detail": "Confirm deletion, verify a current backup exists, and send the exact record_id."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        with transaction.atomic():
            locked_record = config["model"].objects.select_for_update().filter(pk=record.pk).first()
            if locked_record is None:
                return Response({"detail": "Record not found."}, status=status.HTTP_404_NOT_FOUND)
            record_key = str(locked_record.pk)
            locked_record.delete()
            DeveloperAuditLog.objects.create(
                actor=request.user,
                action="delete",
                model_key=model_key,
                record_id=record_key,
                changed_fields=[],
            )
    except DatabaseError:
        return Response({"detail": "The record could not be deleted because it is protected by related data."}, status=status.HTTP_409_CONFLICT)
    return Response({"deleted": True, "model_key": model_key, "record_id": str(record.pk)})


@api_view(["GET"])
@permission_classes([IsAdminUser])
def developer_audit_logs(request):
    page = _serialize_page_size(request.query_params.get("page", "1"))
    page_size = _serialize_page_size(request.query_params.get("page_size", "25"))
    if page is None or page_size is None:
        return Response({"detail": "Page and page size must be positive integers."}, status=status.HTTP_400_BAD_REQUEST)
    if page_size > 100:
        return Response({"detail": "Page size cannot exceed 100 records."}, status=status.HTTP_400_BAD_REQUEST)
    queryset = DeveloperAuditLog.objects.select_related("actor").all()
    search = request.query_params.get("q", "").strip()
    if len(search) > 200:
        return Response({"detail": "Search text cannot exceed 200 characters."}, status=status.HTTP_400_BAD_REQUEST)
    if search:
        queryset = queryset.filter(
            Q(actor__email__icontains=search)
            | Q(model_key__icontains=search)
            | Q(record_id__icontains=search)
            | Q(action__icontains=search)
        )
    paginator = Paginator(queryset, page_size)
    current_page = paginator.get_page(page)
    return Response({
        "count": paginator.count,
        "page": current_page.number,
        "page_size": page_size,
        "num_pages": paginator.num_pages,
        "results": [{
            "id": item.pk,
            "actor_email": item.actor.email if item.actor_id else "Deleted account",
            "action": item.action,
            "model_key": item.model_key,
            "record_id": item.record_id,
            "changed_fields": item.changed_fields,
            "created_at": item.created_at.isoformat(),
        } for item in current_page.object_list],
    })
