# PATH: apps/ai/urls.py

from django.urls import path
from .views import StartChatSessionView, ChatSessionHistoryView, ClearChatSessionView, AuditLogListView
from .admin_views import StartAdminChatSessionView
from .session_views import ChatSessionListView, AdminChatSessionListView, DeleteChatSessionView
from .admin_action_views import ConfirmAdminActionView, CancelAdminActionView
from .upload_views import ChatUploadView
from .feedback_views import MessageFeedbackView

urlpatterns = [
    # Customer chat — session lifecycle
    path('session/start/', StartChatSessionView.as_view(), name='chat-session-start'),
    path('sessions/', ChatSessionListView.as_view(), name='chat-session-list'),
    path('session/<str:session_key>/history/', ChatSessionHistoryView.as_view(), name='chat-session-history'),
    path('session/<str:session_key>/clear/', ClearChatSessionView.as_view(), name='chat-session-clear'),
    path('session/<str:session_key>/', DeleteChatSessionView.as_view(), name='chat-session-delete'),

    # Admin chat — session lifecycle
    path('admin/session/start/', StartAdminChatSessionView.as_view(), name='admin-chat-session-start'),
    path('admin/sessions/', AdminChatSessionListView.as_view(), name='admin-chat-session-list'),

    # Admin — structured confirm/cancel (Requirement 5)
    path('admin/action/<str:action_id>/confirm/', ConfirmAdminActionView.as_view(), name='admin-action-confirm'),
    path('admin/action/<str:action_id>/cancel/', CancelAdminActionView.as_view(), name='admin-action-cancel'),

    # File upload (Requirement 8)
    path('upload/', ChatUploadView.as_view(), name='chat-upload'),

    # Message feedback (Requirement 9)
    path('message/<int:message_id>/feedback/', MessageFeedbackView.as_view(), name='chat-message-feedback'),
]

audit_log_urlpatterns = [
    path('', AuditLogListView.as_view(), name='audit-log-list'),
]