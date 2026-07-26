# PATH: apps/ai/admin_action_views.py
#
# Requirement 5, Change 2 — REST confirm/cancel endpoints for structured
# admin actions (the button-based alternative to typing "haan"/"nahi").

from rest_framework import permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.users.permissions import IsAdmin
from apps.ai.admin_tools.pending_actions import get_pending_action
from apps.ai.admin_tools.registry import execute_pending_action_by_id, cancel_pending_action_by_id

from apps.ai.mixins import ChatAuthErrorMixin
from apps.ai.throttles import ChatUserRateThrottle

def _check_ownership(request, action_id):
    """Returns None if OK, or a Response object if the request should be rejected."""
    pending = get_pending_action(action_id)
    if pending is None or pending.get('resolved'):
        return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
    if pending['user_id'] != request.user.id:
        return Response({'error': 'You are not authorized to act on this confirmation.'}, status=status.HTTP_403_FORBIDDEN)
    return None


class ConfirmAdminActionView(ChatAuthErrorMixin, APIView):
    """POST /api/v1/chat/admin/action/{action_id}/confirm/"""
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    throttle_classes = [ChatUserRateThrottle]

    def post(self, request, action_id):
        rejection = _check_ownership(request, action_id)
        if rejection is not None:
            return rejection

        result_status, _ = execute_pending_action_by_id(action_id)

        if result_status == 'not_found':
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if result_status == 'expired':
            return Response({'error': 'This confirmation has expired. Please repeat the request.'}, status=status.HTTP_410_GONE)

        return Response({'action_id': action_id, 'status': 'confirmed'}, status=status.HTTP_200_OK)


class CancelAdminActionView(ChatAuthErrorMixin, APIView):
    """POST /api/v1/chat/admin/action/{action_id}/cancel/"""
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    throttle_classes = [ChatUserRateThrottle]

    def post(self, request, action_id):
        rejection = _check_ownership(request, action_id)
        if rejection is not None:
            return rejection

        result_status = cancel_pending_action_by_id(action_id)

        if result_status == 'not_found':
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if result_status == 'expired':
            return Response({'error': 'This confirmation has expired. Please repeat the request.'}, status=status.HTTP_410_GONE)

        return Response({'action_id': action_id, 'status': 'cancelled'}, status=status.HTTP_200_OK)