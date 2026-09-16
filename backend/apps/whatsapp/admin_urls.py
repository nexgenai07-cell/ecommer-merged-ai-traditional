# PATH: apps/whatsapp/admin_urls.py

from django.urls import path
from .views import (
    AdminWhatsAppLogsView,
    AdminWhatsAppSessionsView,
    AdminWhatsAppConversationsView,
)

# mounted at /api/v1/admin/whatsapp/ in core/urls.py
admin_whatsapp_urlpatterns = [
    path('logs/', AdminWhatsAppLogsView.as_view(), name='admin-whatsapp-logs'),
    path('sessions/', AdminWhatsAppSessionsView.as_view(), name='admin-whatsapp-sessions'),
    path('conversations/', AdminWhatsAppConversationsView.as_view(), name='admin-whatsapp-conversations'),
]