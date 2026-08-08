from django.urls import path

from apps.analysis import api

app_name = "analysis"

urlpatterns = [
    path("suggestions/", api.suggestion_chips, name="suggestions"),
]
