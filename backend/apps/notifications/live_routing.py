# PATH: apps/notifications/live_routing.py
#
# FLOW: core/asgi.py se yahan aata hai jab URL "ws/live/" match kare.
# -> Agli file: apps/notifications/live_consumers.py

from django.urls import re_path
from .live_consumers import LiveConsumer   # FLOW -> live_consumers.py

live_websocket_urlpatterns = [
    re_path(r'^ws/live/$', LiveConsumer.as_asgi()),
]
