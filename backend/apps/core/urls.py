from django.urls import path

from apps.core import api

app_name = "core"

urlpatterns = [
    path("sessions/", api.create_session, name="session-create"),
    # GET hydrates, DELETE purges — see docs/PLAN.md §6.
    path("sessions/current/", api.current_session, name="session-current"),
    path("sessions/demo/", api.seed_demo, name="session-demo"),
]
