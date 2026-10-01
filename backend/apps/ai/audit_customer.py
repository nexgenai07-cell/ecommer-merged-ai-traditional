# PATH: apps/ai/audit_customer.py
#
# Works out WHICH CUSTOMER an audit-log row is about, so the Audit Logs
# page can show the customer's name next to the Entity ID (e.g.
# "complaint 12 - Ayesha Khan").
#
# UPDATED (Oct 2026): entity_names_for_logs() at the bottom extends this to
# EVERY entity type - product / inventory -> product name, category ->
# category name, discount -> discount code - so the Entity ID column can
# show "7 - Product name" for those rows too. Customer-linked entities
# (order, payment, return, complaint, notification) keep showing the
# customer's name exactly as before.
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


# ============================================================
# NEW (Oct 2026): name of the ENTITY itself, for the Entity ID column.
#
#   order / payment / return / complaint / notification -> customer name
#       (unchanged - same value customer_names_for_logs() gives)
#   product / inventory -> Product.name   (inventory rows store the
#                                          product's id)
#   category            -> Category.name
#   discount            -> Discount.code
#
# The name is read LIVE from the record (matched on entity + entity_id), so
# if a product / category is renamed later, the old log rows show the new
# name too. A soft-deleted record (is_delete=True) still has its row, so
# its name still shows. Only when the row is gone completely does it fall
# back to the name saved inside the log's own old_data / new_data (if any);
# otherwise None. One query per entity type, not one per row. Never raises.
# ============================================================

_NAMED_ENTITIES = ("product", "inventory", "category", "discount")


def _saved_name(log):
    """Name stored inside the log row itself (fallback for a hard-deleted
    record). Looks at old_data / new_data and, for AI-flow logs, at the
    payload / result inside new_data."""
    def pick(d):
        if not isinstance(d, dict):
            return None
        for key in ("name", "code"):
            value = d.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    for data in (log.new_data, log.old_data):
        found = pick(data)
        if found:
            return found
        if isinstance(data, dict):
            for part in ("payload", "result"):
                inner = data.get(part)
                found = pick(inner)
                if found:
                    return found
                if isinstance(inner, dict):
                    for key in ("product", "category", "discount"):
                        found = pick(inner.get(key))
                        if found:
                            return found
    return None


def entity_names_for_logs(logs, customer_names=None):
    """
    Returns {(entity, entity_id): name} for the given AuditLog rows.
    customer_names: pass the result of customer_names_for_logs(logs) if you
    already have it, to avoid computing it twice.
    """
    try:
        return _build_entity_names(logs, customer_names)
    except Exception:
        logger.exception("entity_names_for_logs failed")
        return dict(customer_names or {})


def _build_entity_names(logs, customer_names):
    if customer_names is None:
        customer_names = customer_names_for_logs(logs)

    result = dict(customer_names)

    ids = {e: set() for e in _NAMED_ENTITIES}
    for log in logs:
        if log.entity in ids and log.entity_id is not None:
            ids[log.entity].add(log.entity_id)

    found = {e: {} for e in _NAMED_ENTITIES}

    product_ids = ids["product"] | ids["inventory"]
    if product_ids:
        from apps.products.models import Product

        for pk, name in Product.objects.filter(id__in=product_ids).values_list("id", "name"):
            found["product"][pk] = name
            found["inventory"][pk] = name

    if ids["category"]:
        from apps.categories.models import Category

        for pk, name in Category.objects.filter(id__in=ids["category"]).values_list("id", "name"):
            found["category"][pk] = name

    if ids["discount"]:
        from apps.products.models import Discount

        for pk, code in Discount.objects.filter(id__in=ids["discount"]).values_list("id", "code"):
            found["discount"][pk] = code

    for log in logs:
        if log.entity not in ids or log.entity_id is None:
            continue
        key = (log.entity, log.entity_id)
        name = found[log.entity].get(log.entity_id) or _saved_name(log)
        if name:
            result[key] = name

    return result