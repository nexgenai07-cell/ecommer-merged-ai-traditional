# PATH: apps/ai/urls.py

from django.urls import path
from .views import StartChatSessionView, ChatSessionHistoryView, ClearChatSessionView, AuditLogListView
from .admin_views import StartAdminChatSessionView
from .session_views import ChatSessionListView, AdminChatSessionListView, DeleteChatSessionView   # NEW
from .admin_action_views import ConfirmAdminActionView, CancelAdminActionView   # NEW


urlpatterns = [
    path('session/start/', StartChatSessionView.as_view(), name='chat-session-start'),
    path('sessions/', ChatSessionListView.as_view(), name='chat-session-list'),            # NEW — Requirement 1
    path('session/<str:session_key>/history/', ChatSessionHistoryView.as_view(), name='chat-session-history'),
    path('session/<str:session_key>/clear/', ClearChatSessionView.as_view(), name='chat-session-clear'),
    path('session/<str:session_key>/', DeleteChatSessionView.as_view(), name='chat-session-delete'),  # NEW — Requirement 2
    path('admin/session/start/', StartAdminChatSessionView.as_view(), name='admin-chat-session-start'),
    path('admin/sessions/', AdminChatSessionListView.as_view(), name='admin-chat-session-list'),  # NEW — Requirement 1
    path('admin/action/<str:action_id>/confirm/', ConfirmAdminActionView.as_view(), name='admin-action-confirm'),  # NEW
    path('admin/action/<str:action_id>/cancel/', CancelAdminActionView.as_view(), name='admin-action-cancel'),    # NEW
]

audit_log_urlpatterns = [
    path('', AuditLogListView.as_view(), name='audit-log-list'),
]