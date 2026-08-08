from django.urls import path

from apps.documents import api

app_name = "documents"

urlpatterns = [
    path("documents/", api.documents, name="document-list"),
    path("documents/paste/", api.paste, name="document-paste"),
    path("documents/<uuid:document_id>/", api.document_detail, name="document-detail"),
]
