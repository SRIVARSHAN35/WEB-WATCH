from django.conf import settings
from django.contrib import admin
from django.db import DatabaseError, connection
from django.http import HttpResponse, JsonResponse
from django.urls import include, path, re_path


def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


def serve_spa(request):
    index_path = getattr(settings, "FRONTEND_INDEX_HTML", None)
    if index_path and index_path.is_file():
        return HttpResponse(index_path.read_text(encoding="utf-8"), content_type="text/html")
    return HttpResponse(
        "<!doctype html><html><head><title>WebWatch</title></head><body>"
        "<h1>WebWatch</h1><p>Frontend production build not found. Run <code>npm run build</code> in the frontend directory.</p>"
        "</body></html>",
        status=503,
        content_type="text/html",
    )


def custom_404_view(request, exception=None):
    if request.path.startswith("/api/"):
        return JsonResponse({"detail": "Not found."}, status=404)
    return serve_spa(request)


handler404 = "config.urls.custom_404_view"

urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path("api/auth/", include("monitor.auth_urls")),
    path("api/developer/", include("monitor.developer_urls")),
    path("api/", include("monitor.urls")),
    re_path(r"^(?!api/|admin/|healthz/).*$", serve_spa, name="spa"),
]
