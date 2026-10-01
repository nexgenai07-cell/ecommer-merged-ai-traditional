# PATH: apps/users/phone_validation.py
#
# NEW (Oct 2026): single place for the Pakistani mobile-number rule used
# when a customer adds / changes their phone number from the Profile page
# (Google sign-in users add their number here for the first time).
#
# Allowed formats (spaces and dashes are removed first):
#   03XXXXXXXXX      -> 11 digits, starts with 03        e.g. 03001234567
#   +923XXXXXXXXX    -> "+92" + 10 digits, starts with 3 e.g. +923001234567
#
# Anything else is rejected: other country codes, 92... without "+",
# 3XXXXXXXXX without the leading 0, letters/symbols, wrong digit count.

import re

_LOCAL_RE = re.compile(r"^03\d{9}$")
_INTL_RE = re.compile(r"^\+923\d{9}$")

PK_PHONE_ERROR = (
    "Enter a valid Pakistani mobile number. It must start with 03 "
    "(11 digits, e.g. 03001234567) or +92 (e.g. +923001234567)."
)


def clean_phone(value):
    """Trim and remove spaces / dashes. Always returns a str."""
    if value is None:
        return ""
    return re.sub(r"[\s-]", "", str(value).strip())


def validate_pk_phone(value):
    """
    Returns (cleaned_phone, error_message). error_message is None when the
    number is valid; cleaned_phone is the value to save (as typed, only
    spaces/dashes removed - the 03 / +92 form the customer chose is kept).
    """
    cleaned = clean_phone(value)

    if not cleaned:
        return cleaned, "Phone number is required."

    if _LOCAL_RE.match(cleaned) or _INTL_RE.match(cleaned):
        return cleaned, None

    return cleaned, PK_PHONE_ERROR


def same_phone(a, b):
    """True when two numbers are the same Pakistani number, whichever
    form they were written in (03001234567 == +923001234567)."""
    da = re.sub(r"\D", "", clean_phone(a))
    db = re.sub(r"\D", "", clean_phone(b))
    if not da or not db:
        return False
    return da[-10:] == db[-10:]