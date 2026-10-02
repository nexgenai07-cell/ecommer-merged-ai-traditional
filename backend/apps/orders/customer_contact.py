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
#
# The number is shown EXACTLY as stored - no hiding of dummy/placeholder
# numbers. Format rules are enforced when a number is SAVED, not here.

def resolve_customer_phone(customer):
    user = getattr(customer, "user", None)
    user_phone = (getattr(user, "phone", None) or "").strip()
    if user_phone:
        return user_phone
    return (customer.phone or "").strip()


def resolve_customer_name(customer):
    user = getattr(customer, "user", None)
    return ((getattr(user, "name", "") or customer.name or "")).strip()