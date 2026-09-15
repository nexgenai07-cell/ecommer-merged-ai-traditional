# PATH: apps/ai/ws_auth.py
#
# Requirement 13. Token WebSocket URL mein query param se aata hai:
#   ws://host/ws/chat/<session_key>/?token=<jwt>
# Optional hai (backward-compat) — agar frontend abhi token nahi bhej
# rahi, sab kuch pehle jaisa chalega; jab frontend ise add karegi,
# expiry-checking automatically kaam karne lagegi.

from urllib.parse import parse_qs
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import TokenError


def extract_token_from_scope(scope) -> str | None:
    query_string = scope.get('query_string', b'').decode()
    params = parse_qs(query_string)
    tokens = params.get('token')
    return tokens[0] if tokens else None


def is_token_expired_or_invalid(token: str) -> bool:
    try:
        AccessToken(token)
        return False
    except TokenError:
        return True


# NEW (Sep 2026 — Complaint chat live updates via WebSockets).
# ChatConsumer/AdminChatConsumer never needed to resolve the actual user
# from this token — they identify the user via ChatSession.user (set
# earlier over REST) and only use this token to check expiry. The
# complaint chat has no such session-mapping table, so it needs the real
# user identity straight from the token itself (same identity DRF's
# JWTAuthentication would resolve for this token on a normal request) —
# this is that lookup, kept here since it's the same token/claims this
# file already works with.
def get_user_from_token(token: str):
    """Returns the active User this access token belongs to, or None if
    the token is missing/invalid/expired or the user no longer exists/
    is inactive."""
    if not token:
        return None

    try:
        validated = AccessToken(token)
        user_id = validated['user_id']
    except (TokenError, KeyError):
        return None

    User = get_user_model()
    return User.objects.filter(id=user_id, is_active=True).first()