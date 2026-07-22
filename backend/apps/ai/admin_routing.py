# PATH: apps/ai/admin_routing.py

# FLOW: core/asgi.py se yahan aata hai jab URL "ws/admin-chat/<session_key>/"
# match kare.

# → Agli file: apps/ai/admin_consumers.py

from django.urls import re_path
from .admin_consumers import AdminChatConsumer      # FLOW → apps/ai/admin_consumers.py

admin_websocket_urlpatterns = [
    re_path(r'ws/admin-chat/(?P<session_key>[^/]+)/$', AdminChatConsumer.as_asgi()),
]