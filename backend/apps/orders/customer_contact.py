# PATH: apps/orders/customer_contact.py
#
# NEW (Oct 2026 - Customers page vs CSV export mismatch).
#
# ONE place that decides which name / phone is shown for a Customer, used
# by BOTH the admin Customers list (CustomerAdminSerializer) and the
# Customers CSV export (AnalyticsExportView._export_customers), so the
# screen and the exported file can never disagree again.
#
# WHY THEY DISAGREED: the list read User.phone first, but the export read
# only the Customer.phone COPY. That copy can be blank/old - e.g. a Google
# sign-up has no phone at creation, and apps/users/customer_sync.py skips
# the copy when another customer in the store already has that number.
#
# The customer's ACCOUNT (User) is the source of truth; the Customer row
# is only a fallback (guest customers have no account).
#   name  : User.name  -> Customer.name
#   phone : User.phone -> Customer.phone

import re


def is_placeholder_phone(phone):
    """True for empty values and dummy numbers made only of zeros
    (e.g. 00000000000) - these are not real phone numbers."""
    digits = re.sub(r"\D", "", phone or "")
    return not digits or set(digits) == {"0"}


def resolve_customer_phone(customer):
    user = getattr(customer, "user", None)
    for candidate in (getattr(user, "phone", None), customer.phone):
        if candidate and not is_placeholder_phone(candidate):
            return candidate.strip()
    return ""


def resolve_customer_name(customer):
    user = getattr(customer, "user", None)
    return ((getattr(user, "name", "") or customer.name or "")).strip()