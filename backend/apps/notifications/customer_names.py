# PATH: apps/notifications/customer_names.py
#
# Works out WHICH CUSTOMER an admin notification is about, so the admin
# notification list can show the customer's name ("New order received -
# Ayesha Khan").
#
# Admin notifications don't store the customer directly - they only carry
# reference_type + reference_id (order -> order_number, return -> id,
# complaint -> id). The customer is looked up from that record. Notifications
# with no customer behind them (low stock, sales report, manual admin
# messages, ...) simply get None.
#
# The name is read LIVE from the customer's account when there is one, so
# it always matches what the customer currently has on their profile.
#
# Never raises: a lookup problem returns no name instead of breaking the
# notification list.

import logging

logger = logging.getLogger(__name__)

_CUSTOMER_REF_TYPES = ("order", "return", "complaint")


def _name_of(customer):
    if customer is None:
        return None
    user = getattr(customer, "user", None)
    return (user.name if user is not None and user.name else customer.name) or None


def customer_names_for(notifications):
    """
    Returns {(reference_type, str(reference_id)): customer_name} for every
    notification in the iterable that points at an order/return/complaint.
    Uses 1 query per reference type (not 1 per notification).
    """
    try:
        return _build(notifications)
    except Exception:
        logger.exception("customer_names_for failed")
        return {}


def _build(notifications):
    refs = {t: set() for t in _CUSTOMER_REF_TYPES}
    for n in notifications:
        if n.reference_type in refs and n.reference_id:
            refs[n.reference_type].add(str(n.reference_id))

    result = {}

    if refs["order"]:
        from apps.orders.models import Order

        for o in Order.objects.filter(
            order_number__in=refs["order"]
        ).select_related("customer__user"):
            result[("order", str(o.order_number))] = _name_of(o.customer)

    ids = {t: {int(v) for v in refs[t] if str(v).isdigit()} for t in ("return", "complaint")}

    if ids["return"]:
        from apps.returns.models import Return

        for r in Return.objects.filter(id__in=ids["return"]).select_related(
            "customer__user"
        ):
            result[("return", str(r.id))] = _name_of(r.customer)

    if ids["complaint"]:
        from apps.returns.models import Complaint

        for c in Complaint.objects.filter(
            id__in=ids["complaint"]
        ).select_related("customer__user"):
            result[("complaint", str(c.id))] = _name_of(c.customer)

    return result
