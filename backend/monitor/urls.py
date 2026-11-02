from rest_framework.routers import DefaultRouter
from django.urls import path

from .demo_views import monitor_demo_status
from .views import (
    WebsiteViewSet, alert_detail, alert_mark_read, alerts_list,
    alerts_mark_all_read, alerts_unread_count, account_profile,
    account_settings, account_history, whatsapp_notification_settings,
    whatsapp_test_message, whatsapp_notification_history,
)


router = DefaultRouter()
router.register("websites", WebsiteViewSet, basename="website")

urlpatterns = [
    path("monitor-demo/status/", monitor_demo_status, name="monitor-demo-status"),
    path("alerts/", alerts_list, name="alerts-list"),
    path("alerts/unread-count/", alerts_unread_count, name="alerts-unread-count"),
    path("alerts/mark-all-read/", alerts_mark_all_read, name="alerts-mark-all-read"),
    path("alerts/<int:alert_id>/", alert_detail, name="alert-detail"),
    path("alerts/<int:alert_id>/mark-read/", alert_mark_read, name="alert-mark-read"),
    path("account/profile/", account_profile, name="account-profile"),
    path("account/settings/", account_settings, name="account-settings"),
    path("account/history/", account_history, name="account-history"),
    path("notifications/whatsapp/settings/", whatsapp_notification_settings, name="whatsapp-settings"),
    path("notifications/whatsapp/test/", whatsapp_test_message, name="whatsapp-test"),
    path("notifications/whatsapp/history/", whatsapp_notification_history, name="whatsapp-history"),
    *router.urls,
]
