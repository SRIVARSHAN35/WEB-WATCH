from django.urls import path

from . import developer_views


urlpatterns = [
    path("overview/", developer_views.developer_overview, name="developer-overview"),
    path("tables/", developer_views.developer_tables, name="developer-tables"),
    path("tables/<str:model_key>/records/", developer_views.developer_records, name="developer-records"),
    path(
        "tables/<str:model_key>/records/<str:record_id>/",
        developer_views.developer_record_detail,
        name="developer-record-detail",
    ),
    path("audit-logs/", developer_views.developer_audit_logs, name="developer-audit-logs"),
]
