# PATH: apps/ai/language_preference.py
#
# FLOW: apps/ai/agents/shopping_agent.py se yahan aata hai. Jab customer
# ek language-chip select karta hai ("Wanna talk in Roman Urdu?" waghera),
# uski choice yahan Redis (django cache) mein session_key ke against
# "sticky" store hoti hai — taake agle turns mein bhi wahi language use ho,
# chahe agla message chhota ho ("haan", "ok") jisse per-message script
# detection sahi guess na kar pata. Bilkul wahi pattern jo
# admin_tools/pending_actions.py mein already use ho raha hai.

from django.core.cache import cache

LANGUAGE_PREF_TTL_SECONDS = 60 * 60 * 6  # 6 hours — ek poori chat session ke liye kaafi

VALID_LANGUAGES = {'english', 'roman_urdu', 'urdu_script'}

# Suggestion-chip label <-> internal language code — ek hi jagah maintained,
# suggestions.py aur shopping_agent.py dono isay use karte hain taake
# labels kabhi out-of-sync na hon.
LANGUAGE_CHIPS = {
    'english':     'Wanna talk in English?',
    'roman_urdu':  'Wanna talk in Roman Urdu?',
    'urdu_script': 'Wanna talk in pure Urdu?',
}


def _cache_key(session_key: str) -> str:
    return f"chat_language_pref:{session_key}"


def get_language_preference(session_key: str):
    """Returns 'english' | 'roman_urdu' | 'urdu_script' | None (kabhi select nahi ki)."""
    return cache.get(_cache_key(session_key))


def set_language_preference(session_key: str, language: str):
    if language in VALID_LANGUAGES:
        cache.set(_cache_key(session_key), language, timeout=LANGUAGE_PREF_TTL_SECONDS)


def detect_language_selection(user_message: str):
    """
    Agar customer ne exactly ek language-chip button tap kiya hai (jiska
    text hi message ban kar aata hai — chip tap karne par frontend usi
    label ko chat message ki tarah bhejta hai), uska internal language
    code return karta hai. Warna None — normal messages is se untouched
    rehte hain.
    """
    if not user_message:
        return None
    normalized = user_message.strip().lower().rstrip('?').strip()
    for code, label in LANGUAGE_CHIPS.items():
        if normalized == label.lower().rstrip('?').strip():
            return code
    return None