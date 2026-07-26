# PATH: apps/ai/ws_auth.py
#
# Requirement 13. Token WebSocket URL mein query param se aata hai:
#   ws://host/ws/chat/<session_key>/?token=<jwt>
# Optional hai (backward-compat) — agar frontend abhi token nahi bhej
# rahi, sab kuch pehle jaisa chalega; jab frontend ise add karegi,
# expiry-checking automatically kaam karne lagegi.

from urllib.parse import parse_qs
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