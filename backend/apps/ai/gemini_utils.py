# PATH: apps/ai/gemini_utils.py

# FLOW: Ye file kisi bhi single "step" mein nahi hai — ye ek UTILITY hai
# jo shopping_agent.py, admin_agent.py, aur embedding-wali tools (search)
# SAB use karte hain jab bhi Gemini/Groq ko call karna ho.

import time
from django.conf import settings


class GeminiKeyManager:
    def __init__(self, keys):
        if not keys:
            raise ValueError(
                "GEMINI_API_KEYS khali hai — settings mein kam az kam 1 API key honi chahiye."
            )
        self.keys = keys
        self.index = 0

    @property
    def current_key(self):
        return self.keys[self.index]        # FLOW: agent files isi property se current active key uthate hain

    def rotate(self):
        self.index = (self.index + 1) % len(self.keys)
        return self.current_key

    def total_keys(self):
        return len(self.keys)


gemini_keys = GeminiKeyManager(settings.GEMINI_API_KEYS)


def is_quota_error(exception) -> bool:
    """429 / RESOURCE_EXHAUSTED — is key ki quota khatam ho chuki hai."""
    msg = str(exception)
    return '429' in msg or 'RESOURCE_EXHAUSTED' in msg or 'quota' in msg.lower()


def is_transient_error(exception) -> bool:
    """
    503 / UNAVAILABLE / 'high demand' — Google ka server temporarily
    overloaded hai. Key ka koi qasoor nahi, isliye rotate nahi karte —
    thodi dair ruk kar SAMEI key se dobara try karte hain.
    """
    msg = str(exception)
    lower_msg = msg.lower()
    return '503' in msg or 'UNAVAILABLE' in msg or 'overloaded' in lower_msg or 'high demand' in lower_msg


TRANSIENT_RETRY_ATTEMPTS = 2       # 503 aane par samei key se kitni baar dobara try karein
TRANSIENT_RETRY_DELAY_SECONDS = 3  # har retry se pehle kitni dair rukein


def call_with_fallback(attempt_fn, fallback_fns=None):
    """
    Args:
        attempt_fn:    Gemini ke sath ek koshish (rotate/retry logic pehle jaisi)
        fallback_fns:  OPTIONAL — list of zero-arg functions, TARTEEB (order)
                       se ek-ek karke try hoti hain jab sari Gemini keys
                       exhaust ho jayen. Har fallback apna alag provider/model
                       istemal kare (taake har ek ki apni alag quota bucket ho).
    """
    last_error = None

    for key_attempt in range(gemini_keys.total_keys()):
        moved_to_next_key = False

        for transient_attempt in range(TRANSIENT_RETRY_ATTEMPTS + 1):
            try:
                return attempt_fn()         # FLOW: yahan se agent ka poora LLM+tools invoke chalta hai
            except Exception as e:
                last_error = e

                if is_transient_error(e) and transient_attempt < TRANSIENT_RETRY_ATTEMPTS:
                    time.sleep(TRANSIENT_RETRY_DELAY_SECONDS)
                    continue

                if is_quota_error(e) or is_transient_error(e):
                    moved_to_next_key = True
                    break

                raise

        if moved_to_next_key:
            gemini_keys.rotate()

    # Sari Gemini keys exhaust ho chuki hain — fallbacks ek-ek karke try karo
    fallback_errors = []
    for fallback_fn in (fallback_fns or []):
        try:
            return fallback_fn()        # FLOW: Groq wale attempt yahan chalte hain
        except Exception as fallback_error:
            fallback_errors.append(str(fallback_error))
            continue  # agla fallback try karo

    error_summary = f"Gemini error: {last_error}"
    if fallback_errors:
        error_summary += " | " + " | ".join(f"Fallback error: {e}" for e in fallback_errors)

    raise Exception(
        f"Sari Gemini keys aur fallback providers ki quota khatam ho chuki hai. {error_summary}"
    )