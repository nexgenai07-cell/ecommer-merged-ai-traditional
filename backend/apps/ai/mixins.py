# PATH: apps/ai/mixins.py

from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated


class ChatAuthErrorMixin:
    """
    Requirement 13 — chat-related REST endpoints ke liye JWT expired/invalid
    hone par exact {"error": "Token expired or invalid."} shape (DRF ka
    default {"detail": "..."} nahi).
    """
    def handle_exception(self, exc):
        if isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
            return Response({'error': 'Token expired or invalid.'}, status=status.HTTP_401_UNAUTHORIZED)
        return super().handle_exception(exc)