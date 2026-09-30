# PATH: apps/ai/audit_customer.py
#
# Works out WHICH CUSTOMER an audit-log row is about, so the Audit Logs
# page can show the customer's name next to the Entity ID (e.g.
# "complaint 12 - Ayesha Khan").
#
# AuditLog only stores entity + entity_id, so the customer is looked up
# from that record at read time (this also covers OLD log rows, not just
# new ones):
#   order        -> Order.customer
#   payment      -> Payment.order.customer
#   return       -> Return.customer
#   complaint    -> Complaint.customer
#   notification -> Notification.user (only when sent to one specific
#                   customer; a broadcast has no single customer)
# Everything else (product, category, inventory, ...) has no customer and
# gets None. If the record was deleted since, None as well.
#
# The name is read live from the customer's account when there is one.
# Never raises: a lookup problem returns no name instead of breaking the
# audit log list.

import logging

logger = logging.getLogger(__name__)

_ENTITIES = ("order", "payment", "return", "complaint", "notification")


def _name_of_customer(customer):
    if customer is None:
        return None
    user = getattr(customer, "user", None)
    return (user.name if user is not None and user.name else customer.name) or None


def customer_names_for_logs(logs):
    """
    Returns {(entity, entity_id): customer_name} for the given AuditLog rows.
    One query per entity type (not one per row).
    """
    try:
        return _build(logs)
    except Exception:
        logger.exception("customer_names_for_logs failed")
        return {}


def _build(logs):
    ids = {e: set() for e in _ENTITIES}
    for log in logs:
        if log.entity in ids and log.entity_id is not None:
            ids[log.entity].add(log.entity_id)

    result = {}

    if ids["order"]:
        from apps.orders.models import Order

        for o in Order.objects.filter(id__in=ids["order"]).select_related("customer__user"):
            result[("order", o.id)] = _name_of_customer(o.customer)

    if ids["payment"]:
        from apps.orders.models import Payment

        for p in Payment.objects.filter(id__in=ids["payment"]).select_related(
            "order__customer__user"
        ):
            order = getattr(p, "order", None)
            result[("payment", p.id)] = _name_of_customer(
                order.customer if order is not None else None
            )

    if ids["return"]:
        from apps.returns.models import Return

        for r in Return.objects.filter(id__in=ids["return"]).select_related("customer__user"):
            result[("return", r.id)] = _name_of_customer(r.customer)

    if ids["complaint"]:
        from apps.returns.models import Complaint

        for c in Complaint.objects.filter(id__in=ids["complaint"]).select_related(
            "customer__user"
        ):
            result[("complaint", c.id)] = _name_of_customer(c.customer)

    if ids["notification"]:
        from apps.notifications.models import Notification

        for n in Notification.objects.filter(id__in=ids["notification"]).select_related("user"):
            result[("notification", n.id)] = (
                n.user.name if n.user is not None and n.user.role == "customer" else None
            )

    return result
