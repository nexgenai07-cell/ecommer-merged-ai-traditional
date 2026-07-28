# PATH: apps/ai/admin_action_views.py
#
# Requirement 5, Change 2 — REST confirm/cancel endpoints for structured
# admin actions (the button-based alternative to typing "haan"/"nahi").
#
# FIX — the confirm button used to execute the action but then just return
# an HTTP 200 and stop. It never told the open WebSocket connection (a
# completely separate connection from this HTTP request) that anything
# happened, and it never checked whether the underlying action actually
# succeeded before reporting "confirmed". The text-based "haan" flow
# worked because it runs INSIDE the WebSocket consumer, so the LLM's
# reply naturally goes out over that same connection. This file now:
#   1. Builds a proper confirmation/failure message + metadata from the
#      real executor result (apps/ai/admin_response_metadata.py).
#   2. Saves that as a ChatMessage (so chat history stays consistent).
#   3. Pushes it over the channel layer to "admin_chat_<session_key>",
#      which apps/ai/admin_consumers.py now has a handler for.

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from rest_framework import permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.users.permissions import IsAdmin
from apps.ai.models import ChatSession, ChatMessage
from apps.ai.admin_tools.pending_actions import get_pending_action
from apps.ai.admin_tools.registry import execute_pending_action_by_id, cancel_pending_action_by_id
from apps.ai.admin_response_metadata import describe_executed_admin_action, build_executed_admin_metadata

from apps.ai.mixins import ChatAuthErrorMixin
from apps.ai.throttles import ChatUserRateThrottle


def _get_ownership_checked_pending(request, action_id):
    """Returns (pending_dict, None) if OK, or (None, error_response) if the
    request should be rejected. Fetched once up-front so we still have
    session_key/tool_name available even after execute/cancel resolves it."""
    pending = get_pending_action(action_id)
    if pending is None or pending.get('resolved'):
        return None, Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
    if pending['user_id'] != request.user.id:
        return None, Response({'error': 'You are not authorized to act on this confirmation.'}, status=status.HTTP_403_FORBIDDEN)
    return pending, None


def _push_admin_chat_message(session_key: str, message: str, metadata: dict):
    """Saves the message to chat history AND pushes it live over the
    WebSocket group for this admin session, mirroring the shape
    AdminChatConsumer.receive() sends for the normal text-chat flow."""
    chat_session = ChatSession.objects.filter(session_key=session_key).first()
    if chat_session is not None:
        ChatMessage.objects.create(session=chat_session, sender='ai', message=message, metadata=metadata)

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return  # no channel layer configured — nothing more we can do

    async_to_sync(channel_layer.group_send)(
        f"admin_chat_{session_key}",
        {
            "type": "chat.message",  # -> AdminChatConsumer.chat_message()
            "payload": {
                "type": "message",
                "sender": "ai",
                "message": message,
                "requires_confirmation": False,
                "metadata": metadata,
                "suggestions": [],
            },
        },
    )


class ConfirmAdminActionView(ChatAuthErrorMixin, APIView):
    """POST /api/v1/chat/admin/action/{action_id}/confirm/"""
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    throttle_classes = [ChatUserRateThrottle]

    def post(self, request, action_id):
        pending, rejection = _get_ownership_checked_pending(request, action_id)
        if rejection is not None:
            return rejection

        session_key = pending['session_key']
        tool_name = pending['tool_name']

        # FIX — previously only the status ('executed'/'not_found'/'expired')
        # was kept and the actual result dict was discarded (`_`). That
        # meant even if the underlying executor call failed (e.g. the
        # internal API call errored out), this endpoint still happily
        # reported {"status": "confirmed"} because 'executed' just means
        # "an executor ran", not "it succeeded".
        result_status, result = execute_pending_action_by_id(action_id)

        if result_status == 'not_found':
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if result_status == 'expired':
            return Response({'error': 'This confirmation has expired. Please repeat the request.'}, status=status.HTTP_410_GONE)

        succeeded = isinstance(result, dict) and bool(result.get('success'))
        message = describe_executed_admin_action(tool_name, result)
        metadata = build_executed_admin_metadata(tool_name, result)

        # FIX — this is the actual missing piece from the bug report: push
        # the result back over the WebSocket + save it to chat history.
        _push_admin_chat_message(session_key, message, metadata)

        return Response(
            {
                'action_id': action_id,
                'status': 'confirmed' if succeeded else 'failed',
                'success': succeeded,
                'message': message,
            },
            status=status.HTTP_200_OK,
        )


class CancelAdminActionView(ChatAuthErrorMixin, APIView):
    """POST /api/v1/chat/admin/action/{action_id}/cancel/"""
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    throttle_classes = [ChatUserRateThrottle]

    def post(self, request, action_id):
        pending, rejection = _get_ownership_checked_pending(request, action_id)
        if rejection is not None:
            return rejection

        session_key = pending['session_key']

        result_status = cancel_pending_action_by_id(action_id)

        if result_status == 'not_found':
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if result_status == 'expired':
            return Response({'error': 'This confirmation has expired. Please repeat the request.'}, status=status.HTTP_410_GONE)

        # NEW — cancel already correctly does nothing to the underlying data
        # (that part was fine), but it had the same silent-to-the-chat-UI gap
        # as confirm. Added for symmetry so the admin sees an explicit
        # acknowledgement bubble instead of only a card-state change.
        _push_admin_chat_message(
            session_key,
            "Theek hai, ye action cancel kar diya gaya hai — koi change nahi kiya gaya.",
            {'products': [], 'categories': [], 'customers': [], 'analytics': None},
        )

        return Response({'action_id': action_id, 'status': 'cancelled'}, status=status.HTTP_200_OK)