# PATH: apps/ai/admin_tools/pending_actions.py
#
# Requirement 5 — Admin Confirm/Cancel Structured Action.
#
# action_id ab GLOBAL hai (session_key uske andar nahi) — taake REST
# endpoint (jo sirf action_id jaanta hai, session_key nahi) usay dhoondh
# sake. UUID v4 (unguessable), 5-minute business-rule expiry.
#
# Cache TTL (15 min) business-rule expiry (5 min) se JAAN-BOOJH KAR
# zyada rakhi hai — taake "expired" (410) aur "not found/resolved" (404)
# mein farq kiya ja sake. Agar dono ka TTL same hota, expire hone ke
# baad hum kabhi bata hi nahi pate ke action expire hua tha ya kabhi
# tha hi nahi.

import uuid
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta

LOGICAL_TTL_SECONDS = 300      # business rule: 5 minutes
CACHE_SAFETY_TTL_SECONDS = 900  # Redis mein isse zyada dair tak rakhte hain

ACTION_TYPE_CHOICES = [
    'create_product', 'update_product', 'delete_product',
    'create_category', 'update_category', 'delete_category',
    'update_inventory', 'update_order', 'cancel_order',
]


def _cache_key(action_id: str) -> str:
    return f"admin_pending_action:{action_id}"


def create_pending_action(session_key: str, user_id: int, tool_name: str, kwargs: dict, preview: dict) -> dict:
    """
    Returns: {'action_id': ..., 'expires_at': iso_string}
    """
    action_id = str(uuid.uuid4())
    expires_at = timezone.now() + timedelta(seconds=LOGICAL_TTL_SECONDS)

    cache.set(_cache_key(action_id), {
        'session_key': session_key,
        'user_id': user_id,
        'tool_name': tool_name,
        'kwargs': kwargs,
        'preview': preview,
        'expires_at': expires_at.isoformat(),
        'resolved': False,
    }, timeout=CACHE_SAFETY_TTL_SECONDS)

    return {'action_id': action_id, 'expires_at': expires_at.isoformat()}


def get_pending_action(action_id: str) -> dict | None:
    """Raw fetch — no expiry/resolved check. Callers check that themselves."""
    return cache.get(_cache_key(action_id))


def is_expired(pending: dict) -> bool:
    expires_at = timezone.datetime.fromisoformat(pending['expires_at'])
    return timezone.now() > expires_at


def mark_resolved(action_id: str):
    """
    Doesn't delete — keeps the record (still inside its 15-min cache
    window) so a SECOND confirm/cancel attempt on the same action_id
    can still be found and correctly reported as 'already resolved'
    (404) rather than silently vanishing.
    """
    pending = cache.get(_cache_key(action_id))
    if pending is not None:
        pending['resolved'] = True
        cache.set(_cache_key(action_id), pending, timeout=CACHE_SAFETY_TTL_SECONDS)


# Kept for backward-compat with any old call sites expecting this name —
# now just an alias.
def clear_pending_action(action_id: str):
    mark_resolved(action_id)