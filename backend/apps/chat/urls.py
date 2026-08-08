from django.urls import path

from apps.chat import api

app_name = "chat"

urlpatterns = [
    path("chat/", api.chat, name="chat"),
    path("chat/messages/", api.messages, name="chat-messages"),
    path("traces/<uuid:message_id>/", api.trace, name="trace"),
    path("usage/", api.usage, name="usage"),
]
