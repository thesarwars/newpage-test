"""Root URL configuration.

M0 carries a bare liveness probe so `docker compose up` is verifiable end to end.
M1 replaces it with the real ops spine in `apps/core` (`/healthz`, `/readyz`,
`/version`) — see docs/PLAN.md §13.
"""

from django.contrib import admin
from django.http import HttpRequest, JsonResponse
from django.urls import path


def healthz(_request: HttpRequest) -> JsonResponse:
    """Liveness only — deliberately touches no dependency.

    A liveness probe that checks the database turns a slow query into a restart
    storm. Dependency checks belong in /readyz (M1).
    """
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz, name="healthz"),
]
