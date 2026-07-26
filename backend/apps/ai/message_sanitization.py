# PATH: apps/ai/message_sanitization.py
#
# Requirement 12. Design note: raw (validated) message LLM ko diya jata
# hai us turn ke liye (taake AI ka understanding sahi rahe), lekin
# database mein ESCAPED version store hota hai (stored-XSS se bachne
# ke liye, jaisa admin views/exports mein kabhi render ho). Purani
# history se context banate waqt unescape kiya jata hai taake escaped
# entities (&lt; waghera) LLM ko confuse na karein.

import re
import html

MAX_MESSAGE_LENGTH = 2000

DANGEROUS_PATTERNS = [
    re.compile(r'<script\b', re.IGNORECASE),
    re.compile(r'<iframe\b', re.IGNORECASE),
    re.compile(r'\bon\w+\s*=', re.IGNORECASE),  # onclick=, onerror= waghera
]


class MessageValidationError(Exception):
    pass


def validate_message(raw_message: str):
    """Raise MessageValidationError agar message invalid ho — kuch return nahi karta."""
    if not isinstance(raw_message, str):
        raise MessageValidationError("Message must be text.")

    if len(raw_message) > MAX_MESSAGE_LENGTH:
        raise MessageValidationError(f"Message exceeds the maximum length of {MAX_MESSAGE_LENGTH} characters.")

    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(raw_message):
            raise MessageValidationError(
                "Message contains disallowed content (script/iframe/event-handler) and was not sent."
            )


def escape_for_storage(raw_message: str) -> str:
    """Database mein save karne se pehle — HTML escape karta hai."""
    return html.escape(raw_message)


def unescape_for_context(stored_message: str) -> str:
    """Purani history se chat_history banate waqt — LLM ke liye unescape karta hai."""
    return html.unescape(stored_message)