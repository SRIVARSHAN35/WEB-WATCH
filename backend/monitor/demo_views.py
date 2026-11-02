from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponseNotFound, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods


DEMO_STATUS_CACHE_KEY = "webwatch_monitor_demo_status"


@csrf_exempt
@require_http_methods(["GET", "POST"])
def monitor_demo_status(request):
    """Shared demo state is available only when explicitly enabled."""
    if not settings.MONITOR_DEMO_ENABLED:
        return HttpResponseNotFound()

    current_status = cache.get(DEMO_STATUS_CACHE_KEY, "Applied")
    if request.method == "POST":
        current_status = "Rejected" if current_status == "Applied" else "Applied"
        cache.set(DEMO_STATUS_CACHE_KEY, current_status, timeout=None)

    return JsonResponse({"status": current_status})
