"""Routing rules for websocket connections."""
from channels.routing import ProtocolTypeRouter, URLRouter

from django.urls import path

from .consumers import ClientConsumer

application = ProtocolTypeRouter(
    {
        # Client-facing WebSocket Consumers.
        "websocket": URLRouter(
            [path("ws/<slug:session_id>", ClientConsumer.as_asgi())]
        ),
    }
)
