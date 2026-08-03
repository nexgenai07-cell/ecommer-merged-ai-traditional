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
        attempt_fn:    Primary model ke sath ek koshish (Gemini-style key
                       rotate/retry logic — ye ab sirf embeddings calls ke
                       liye asli maayne rakhta hai, jo ek hi provider Gemini
                       use karte hain).
        fallback_fns:  OPTIONAL — list of zero-arg functions, TARTEEB (order)
                       se ek-ek karke try hoti hain jab primary fail ho jaye —
                       chahe wo quota ho, 410/invalid-model ho, timeout ho,
                       ya koi bhi aur error. Har fallback apna alag
                       provider/model istemal kare.
    """
    last_error = None

    for key_attempt in range(gemini_keys.total_keys()):
        move_to_next_key = False

        for transient_attempt in range(TRANSIENT_RETRY_ATTEMPTS + 1):
            try:
                return attempt_fn()         # FLOW: yahan se agent ka poora LLM+tools invoke chalta hai
            except Exception as e:
                last_error = e

                if is_transient_error(e) and transient_attempt < TRANSIENT_RETRY_ATTEMPTS:
                    time.sleep(TRANSIENT_RETRY_DELAY_SECONDS)
                    continue  # SAME key se dobara try

                if is_quota_error(e) or is_transient_error(e):
                    move_to_next_key = True
                    break  # is key ki koshish khatam — agli key try karo

                # FIX: quota/transient KOI NAHI (jaise NVIDIA ka 410 Gone,
                # invalid model, bad request, timeout wagera) — key rotate
                # karne ka koi fayda nahi hota, is liye ab seedha fallback
                # chain try karte hain (pehle ye yahan se `raise` ho jata
                # tha aur fallback_fns tak kabhi pohonchta hi nahi tha)
                return _run_fallbacks(fallback_fns, last_error)

        if move_to_next_key:
            gemini_keys.rotate()

    # Saari Gemini keys quota/transient error se exhaust ho chuki hain —
    # fallbacks ek-ek karke try karo
    return _run_fallbacks(fallback_fns, last_error)


def _run_fallbacks(fallback_fns, last_error):
    fallback_errors = []
    for fallback_fn in (fallback_fns or []):
        try:
            return fallback_fn()        # FLOW: chain ka agla model/provider yahan chalta hai
        except Exception as fallback_error:
            fallback_errors.append(str(fallback_error))
            continue  # agla fallback try karo

    error_summary = f"Primary error: {last_error}"
    if fallback_errors:
        error_summary += " | " + " | ".join(f"Fallback error: {e}" for e in fallback_errors)

    raise Exception(
        f"Primary model aur saare fallback providers fail ho gaye. {error_summary}"
    )


# NEW — CRITICAL PERFORMANCE FIX.
#
# ROOT CAUSE OF 5-6 MINUTE DELAYS: shopping_agent.py aur admin_agent.py
# apna poora LLM-provider fallback chain (NVIDIA model -> NVIDIA model ->
# ... -> Groq model) purane `call_with_fallback()` ke through chalate
# thay. Lekin us function ka outer loop — `for key_attempt in
# range(gemini_keys.total_keys())` — sirf EMBEDDING calls (Gemini) ke
# liye design hua tha, jahan har iteration mein ek ALAG Gemini API key
# try hoti hai.
#
# Agent LLM chain (NVIDIA/Groq) ka Gemini keys se koi lena dena nahi —
# is liye jab NVIDIA ka primary model kabhi overloaded/503 (is_transient_
# error) return karta, ye poora outer loop us EXACT SAME primary model
# ko baar baar dobara try karta rehta — (TRANSIENT_RETRY_ATTEMPTS + 1)
# baar HAR "key" ke liye — matlab agar settings mein 3-5 GEMINI_API_KEYS
# configured hain, to same slow/overloaded model 9-15 dafa try hota,
# har koshish ke beech 3-second sleep, is se pehle ke asal fallback
# chain (deepseek, llama, Groq) tak pohonche. Yahi 5-6 minute ki delay
# ki asal wajah thi.
#
# Fix: agent LLM chain ke liye ye alag, seedha function use karo — Gemini
# key count se koi matlab nahi, sirf EK chhota transient-retry, phir
# seedha fallback chain.
MODEL_TRANSIENT_RETRY_ATTEMPTS = 1        # LLM chain ke liye — embeddings wale se kam, taake jaldi fallback pe jump ho
MODEL_TRANSIENT_RETRY_DELAY_SECONDS = 2


def call_with_model_fallback(attempt_fn, fallback_fns=None):
    """
    LLM agent chain (NVIDIA model -> ... -> Groq model) ke liye. Har
    model ko sirf EK chhoti transient-retry deta hai, phir turant agle
    fallback model pe chala jata hai — Gemini key-count se bilkul
    independent (embeddings ke `call_with_fallback` se ye is liye alag
    hai).
    """
    last_error = None
    for attempt_num in range(MODEL_TRANSIENT_RETRY_ATTEMPTS + 1):
        try:
            return attempt_fn()
        except Exception as e:
            last_error = e
            if is_transient_error(e) and attempt_num < MODEL_TRANSIENT_RETRY_ATTEMPTS:
                time.sleep(MODEL_TRANSIENT_RETRY_DELAY_SECONDS)
                continue
            break  # transient ho ya na ho, ek retry ke baad seedha fallback chain

    return _run_fallbacks(fallback_fns, last_error)