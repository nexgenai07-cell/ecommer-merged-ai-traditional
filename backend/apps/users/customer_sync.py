# PATH: apps/users/customer_sync.py
#
# Keeps the store-scoped Customer rows (apps.orders.models.Customer) in
# step with the account (User) when the user changes their own profile.
#
# WHY THIS EXISTS: Customer.name / phone / email are a COPY taken once,
# when the Customer row is first created (see get_or_create_customer in
# apps/orders/views.py). Nothing updated that copy afterwards, so after a
# profile edit the admin Customers page, order lists/details, returns,
# complaints, e-mails ("Hi <name>") etc. - which all read the Customer
# row - kept showing the OLD name / phone / email. Calling
# sync_customer_profiles() right after a profile change fixes all of
# those places at once, without touching any of them.
#
# Design rules (so no other flow can break):
#   * Called explicitly from the 4 places that change name/phone/email
#     (not from a post_save signal, because User is saved on every login
#     for last_login and a signal would overwrite checkout-edited data).
#   * Only the field(s) that actually changed are synced.
#   * NEVER raises: a sync problem is logged, the profile update itself
#     still succeeds.
#   * Phone: the (phone, store) unique constraint on Customer is
#     respected - if the new number already belongs to another customer
#     in the store, the Customer.phone copy is skipped (User.phone is the
#     source of truth and the admin Customers page already prefers it).

import logging

from django.db import IntegrityError, transaction

logger = logging.getLogger(__name__)


def _digits_variants(*numbers):
    return {n for n in numbers if n}


def sync_customer_profiles(user, fields, old_phone=None):
    """
    fields: iterable containing any of "name", "email", "phone".
    old_phone: the user's phone BEFORE the change (needed for "phone").
    """
    try:
        _sync(user, set(fields), old_phone)
    except Exception:
        logger.exception(
            "Could not sync Customer profile(s) for user %s", user.pk
        )


def _sync(user, fields, old_phone):
    # Local imports: apps.orders imports from apps.users at module load.
    from apps.orders.models import Address, Customer, Order

    customers = list(Customer.objects.filter(user=user))
    if not customers:
        return

    new_phone = user.phone or ""

    for customer in customers:
        # ---- name / email: no uniqueness constraints, plain update ----
        simple = {}
        if "name" in fields and customer.name != user.name:
            simple["name"] = user.name
        if "email" in fields and customer.email != user.email:
            simple["email"] = user.email
        if simple:
            Customer.objects.filter(pk=customer.pk).update(**simple)

        # ---- phone ----
        if "phone" not in fields:
            continue

        # Numbers that count as "the old number of this person": the old
        # account phone and whatever this Customer row held.
        old_numbers = _digits_variants(old_phone, customer.phone)
        old_numbers.discard(new_phone)

        if customer.phone != new_phone:
            try:
                with transaction.atomic():
                    Customer.objects.filter(pk=customer.pk).update(
                        phone=new_phone
                    )
            except IntegrityError:
                logger.warning(
                    "Customer.phone not synced for user %s: %s is already "
                    "used by another customer in this store.",
                    user.pk, new_phone,
                )

        if old_numbers:
            # Saved addresses and orders that carry the person's OLD
            # number (a copy made at registration / checkout) now show
            # the new one. Addresses/orders with any other number (e.g.
            # a family member's number typed for delivery) are untouched.
            Address.objects.filter(
                customer=customer, phone__in=old_numbers
            ).update(phone=new_phone or None)
            Order.objects.filter(
                customer=customer, contact_phone__in=old_numbers
            ).update(contact_phone=new_phone)
