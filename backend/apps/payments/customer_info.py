# PATH: apps/payments/customer_info.py
#
# NEW (Oct 2026): the customer name / phone shown on the admin QR Payments
# pages.
#
# WHY: those pages used to read Customer.name / Customer.phone - a COPY
# made when the Customer row was created (a Google sign-in customer has no
# phone at that moment). The copy is only refreshed when the profile sync
# (apps/users/customer_sync.py) manages to run - it silently skips a phone
# that another customer in the store already uses - so the page could show
# an empty phone ("-") or an old name even though the customer's profile
# had the new values.
#
# The customer's ACCOUNT (User) is the source of truth, so it is read
# first; the copies are only fallbacks (guest customers have no account).
#   name  : User.name  -> Customer.name
#   phone : User.phone -> Customer.phone -> Order.contact_phone


def customer_name_phone(order):
    customer = order.customer
    user = getattr(customer, "user", None)

    name = ((user.name if user else "") or customer.name or "").strip()
    phone = (
        (user.phone if user else "")
        or customer.phone
        or order.contact_phone
        or ""
    )
    return name, phone