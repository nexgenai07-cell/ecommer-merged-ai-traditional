# PATH: apps/ai/admin_tools/pending_actions.py

# FLOW: Ye poore confirmation-gating system ka core hai. registry.py
# ke sab propose_* aur confirm_pending_action functions isay use karte
# hain. Data Redis cache mein store hota hai (django.core.cache).

import uuid
from django.core.cache import cache

PENDING_ACTION_TTL_SECONDS = 600  # 10 minutes — itni dair mein confirm na kare to expire


def create_pending_action(session_key: str, tool_name: str, kwargs: dict, preview: dict) -> str:

    """FLOW: product_tools.py/category_tools.py/inventory_tools.py/order_tools.py
    ke sab propose_* functions se call hota hai. Redis mein ek naya
    entry banta hai, action_id return hota hai."""

    """
    Ek pending (abhi execute nahi hui) action ko cache mein store karta hai.
    Returns: action_id jo admin ko confirmation maangte waqt reference k liye diya jata hai.
    """
    action_id = uuid.uuid4().hex[:12]
    cache_key = f"pending_action:{session_key}:{action_id}"
    cache.set(cache_key, {
        'tool_name': tool_name,
        'kwargs': kwargs,
        'preview': preview,
    }, timeout=PENDING_ACTION_TTL_SECONDS)
    return action_id

def get_pending_action(session_key: str, action_id: str) -> dict | None:

    """FLOW: registry.py ke confirm_pending_action se call hota hai —
    admin ke 'haan' bolne pe Redis se pending action wapis nikalti hai."""

    """Pending action ko cache se nikalta hai (confirm karte waqt use hota hai)."""
    cache_key = f"pending_action:{session_key}:{action_id}"
    return cache.get(cache_key)


def clear_pending_action(session_key: str, action_id: str):

    """FLOW: confirm_pending_action mein, execute hone ke turant baad call hota hai — Redis se hata deta hai."""
    
    """Confirm/reject hone k baad cache se hata dete hain."""
    cache_key = f"pending_action:{session_key}:{action_id}"
    cache.delete(cache_key)