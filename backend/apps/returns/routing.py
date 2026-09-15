# PATH: apps/returns/routing.py
#
# FLOW: core/asgi.py se yahan aata hai jab URL "ws/complaints/<complaint_id>/"
# match kare — same "phone directory" pattern as apps/ai/routing.py.

from django.urls import re_path

from .consumers import ComplaintConsumer

complaint_websocket_urlpatterns = [
    re_path(
        r'ws/complaints/(?P<complaint_id>\d+)/$',
        ComplaintConsumer.as_asgi(),
    ),
]