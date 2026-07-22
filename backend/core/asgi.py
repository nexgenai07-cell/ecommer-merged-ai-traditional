# PATH: core/asgi.py
#
# FLOW: Ye poori app ka ENTRY POINT hai jab bhi koi WebSocket connection
# aati hai (chahe customer ho ya admin). Daphne server isi file ko load
# karta hai.
#
# → HTTP requests: django_asgi_app ko jati hain (normal Django views)
# → WebSocket requests: URLRouter dekhta hai URL kis pattern se match
#   karta hai, phir wahi wali routing file (Step 2) mein jump hoti hai.

import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings.development')

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from apps.ai.routing import websocket_urlpatterns              # FLOW → apps/ai/routing.py (customer chat URLs)
from apps.ai.admin_routing import admin_websocket_urlpatterns   # FLOW → apps/ai/admin_routing.py (admin chat URLs)

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': AuthMiddlewareStack(
        # FLOW: yahan URL ka pattern match hota hai —
        #   ws/chat/<session_key>/       → apps/ai/routing.py → ChatConsumer
        #   ws/admin-chat/<session_key>/ → apps/ai/admin_routing.py → AdminChatConsumer
        URLRouter(websocket_urlpatterns + admin_websocket_urlpatterns)
    ),
})