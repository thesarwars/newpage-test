"""Root URL configuration.

Ops endpoints sit at the root, unversioned: a load balancer health check should
not have to know about `/api/v1`, and these three never change shape.
Everything else is versioned from the first commit — adding `/v2` later is
cheap, retrofitting a version prefix onto a shipped client is not.
"""

from django.contrib import admin
from django.urls import include, path

from apps.core import health

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", health.healthz, name="healthz"),
    path("readyz", health.readyz, name="readyz"),
    path("version", health.version, name="version"),
    path("api/v1/", include("apps.core.urls")),
]
