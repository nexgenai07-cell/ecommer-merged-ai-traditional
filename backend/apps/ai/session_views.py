# PATH: apps/ai/session_views.py
#
# Requirement 1 — List Past Chat Sessions
# Requirement 2 — Soft Delete a Session

from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.users.permissions import IsAdmin
from .models import ChatSession, ChatMessage


def _build_session_list_response(request, base_queryset):
    """
    Shared logic for both customer and admin session-list endpoints —
    same response shape, same title/preview generation rules.
    """
    limit = request.query_params.get('limit', 20)
    offset = request.query_params.get('offset', 0)

    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 20
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0

    # Requirement 1 rules: exclude soft-deleted and guest (user=null) sessions,
    # sort by updated_at descending
    qs = base_queryset.filter(is_deleted=False, user__isnull=False).order_by('-updated_at')

    count = qs.count()
    page = qs[offset:offset + limit]

    sessions_data = []
    for session in page:
        first_user_msg = session.messages.filter(sender='user').order_by('created_at').first()
        last_ai_msg = session.messages.filter(sender='ai').order_by('-created_at').first()

        if first_user_msg:
            title = first_user_msg.message[:40]
        else:
            title = "New Chat"

        preview = last_ai_msg.message[:60] if last_ai_msg else ""

        sessions_data.append({
            'session_key': session.session_key,
            'title': title,
            'preview': preview,
            'started_at': session.started_at.isoformat(),
            'updated_at': session.updated_at.isoformat(),
            'message_count': session.messages.count(),
        })

    return Response({'count': count, 'sessions': sessions_data}, status=status.HTTP_200_OK)


class ChatSessionListView(APIView):
    """GET /api/v1/chat/sessions/ — customer's own past sessions."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        base_qs = ChatSession.objects.filter(user=request.user, channel='customer')
        return _build_session_list_response(request, base_qs)


class AdminChatSessionListView(APIView):
    """GET /api/v1/chat/admin/sessions/ — admin's own past sessions."""
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get(self, request):
        base_qs = ChatSession.objects.filter(user=request.user, channel='admin')
        return _build_session_list_response(request, base_qs)


class DeleteChatSessionView(APIView):
    """
    DELETE /api/v1/chat/session/{session_key}/ — Requirement 2.
    Soft delete only — no rows are physically removed.
    """
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, session_key):
        try:
            session = ChatSession.objects.get(session_key=session_key)
        except ChatSession.DoesNotExist:
            return Response({'error': 'Session not found.'}, status=status.HTTP_404_NOT_FOUND)

        if session.user_id != request.user.id:
            return Response({'error': 'You do not have permission to delete this session.'}, status=status.HTTP_403_FORBIDDEN)

        session.is_deleted = True
        session.deleted_at = timezone.now()
        session.save(update_fields=['is_deleted', 'deleted_at'])

        return Response({
            'session_key': session.session_key,
            'deleted': True,
            'deleted_at': session.deleted_at.isoformat(),
        }, status=status.HTTP_200_OK)