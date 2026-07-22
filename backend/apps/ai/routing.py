# PATH: apps/ai/routing.py
#
# FLOW: core/asgi.py se yahan aata hai jab URL "ws/chat/<session_key>/"
# match kare. Ye sirf ek "phone directory" hai — batata hai kaunsi
# Consumer class is URL ko handle karegi.
#
# → Agli file: apps/ai/consumers.py (ChatConsumer class)

from django.urls import re_path
from .consumers import ChatConsumer   # FLOW → apps/ai/consumers.py

websocket_urlpatterns = [
    re_path(r'ws/chat/(?P<session_key>[^/]+)/$', ChatConsumer.as_asgi()),
]