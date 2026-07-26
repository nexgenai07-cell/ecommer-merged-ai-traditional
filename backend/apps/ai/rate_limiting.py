# PATH: apps/ai/rate_limiting.py
#
# Requirement 11 — WebSocket rate limiting. Redis (django cache) mein
# har key ke recent message-timestamps ki list rakhi jati hai — sliding
# window approach.

from django.core.cache import cache
from django.utils import timezone

MAX_MESSAGES = 10
WINDOW_SECONDS = 10


def check_rate_limit(key: str) -> bool:
    """
    Returns True agar message allow hai, False agar limit exceed ho gayi.
    NOTE: cache.get + cache.set do alag operations hain (atomic nahi) —
    normal chat traffic ke liye theek hai, lekin bohat high-concurrency
    load mein race condition ki chhoti si gunjaish rahegi.
    """
    now = timezone.now().timestamp()
    cache_key = f"ws_rate_limit:{key}"

    timestamps = cache.get(cache_key) or []
    timestamps = [t for t in timestamps if now - t < WINDOW_SECONDS]

    if len(timestamps) >= MAX_MESSAGES:
        return False

    timestamps.append(now)
    cache.set(cache_key, timestamps, timeout=WINDOW_SECONDS + 5)
    return True


def check_all_rate_limits(session_key: str, user_id: int = None, ip: str = None) -> bool:
    """
    Requirement ke mutabiq teen alag layers check hoti hain — session_key,
    user_id (agar logged-in), aur IP (guests ke liye). Sab pass hon tabhi
    message allow hota hai.
    """
    if not check_rate_limit(f"session:{session_key}"):
        return False
    if user_id is not None and not check_rate_limit(f"user:{user_id}"):
        return False
    if user_id is None and ip is not None and not check_rate_limit(f"ip:{ip}"):
        return False
    return True