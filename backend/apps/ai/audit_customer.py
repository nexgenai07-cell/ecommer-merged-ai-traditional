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
# UPDATED (Oct 2026, part 2): now covers EVERY entity that shows up in the
# Audit Logs table: product / inventory / category / discount (as before)
# plus social posts, users, stores and reviews, and any other entity falls
# back to a name saved in its own log rows. A hard-deleted record
# (e.g. "delete_product 64") takes its name from the log rows of the same
# entity + id (the create / update logs of that record stored it).
#
# The name is read LIVE from the record (matched on entity + entity_id), so
# if a product / category is renamed later, the old log rows show the new
# name too. A soft-deleted record (is_delete=True) still has its row, so
# its name still shows. Only when the row is gone completely does it fall
# back to the name saved inside the log's own old_data / new_data (if any);
# otherwise None. One query per entity type, not one per row. Never raises.
# ============================================================

_NAMED_ENTITIES = (
    "product", "inventory", "category", "discount",
    "social_post", "social", "post", "user", "customer", "store", "review",
)

_NAME_KEYS = ("name", "title", "code", "product_name", "caption")


def _clip(text, limit=40):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def _pick(d):
    if not isinstance(d, dict):
        return None
    for key in _NAME_KEYS:
        value = d.get(key)
        if isinstance(value, str) and value.strip():
            return _clip(value)
    return None


def _saved_name(log):
    """Name stored inside the log row itself (fallback for a hard-deleted
    record). Looks at old_data / new_data and, for AI-flow logs, at the
    payload / result inside new_data."""
    for data in (log.new_data, log.old_data):
        found = _pick(data)
        if found:
            return found
        if isinstance(data, dict):
            for part in ("payload", "result"):
                inner = data.get(part)
                found = _pick(inner)
                if found:
                    return found
                if isinstance(inner, dict):
                    for key in ("product", "category", "discount", "post", "user"):
                        found = _pick(inner.get(key))
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


def _safe_lookup(label, fn):
    """One entity type failing (unknown field, missing app) must never
    stop the other types from getting their names."""
    try:
        return fn()
    except Exception:
        logger.exception("audit entity name lookup failed for %s", label)
        return {}


def _lookup_products(ids):
    from apps.products.models import Product
    return dict(Product.objects.filter(id__in=ids).values_list("id", "name"))


def _lookup_categories(ids):
    from apps.categories.models import Category
    return dict(Category.objects.filter(id__in=ids).values_list("id", "name"))


def _lookup_discounts(ids):
    from apps.products.models import Discount
    return dict(Discount.objects.filter(id__in=ids).values_list("id", "code"))


def _lookup_social_posts(ids):
    from apps.social.models import SocialPost
    return {
        pk: _clip(caption) if caption else None
        for pk, caption in SocialPost.objects.filter(id__in=ids).values_list("id", "caption")
    }


def _lookup_users(ids):
    from django.contrib.auth import get_user_model
    return dict(get_user_model().objects.filter(id__in=ids).values_list("id", "name"))


def _lookup_stores(ids):
    from apps.stores.models import Store
    return dict(Store.objects.filter(id__in=ids).values_list("id", "name"))


def _lookup_reviews(ids):
    from apps.products.models import Review
    return {
        r.id: r.product.name
        for r in Review.objects.filter(id__in=ids).select_related("product")
        if r.product_id
    }


_LOOKUPS = {
    "product": _lookup_products,
    "inventory": _lookup_products,   # inventory rows store the product id
    "category": _lookup_categories,
    "discount": _lookup_discounts,
    "social_post": _lookup_social_posts,
    "social": _lookup_social_posts,
    "post": _lookup_social_posts,
    "user": _lookup_users,
    "customer": _lookup_users,
    "store": _lookup_stores,
    "review": _lookup_reviews,
}


def _build_entity_names(logs, customer_names):
    if customer_names is None:
        customer_names = customer_names_for_logs(logs)

    result = dict(customer_names)

    # Entities whose name is the customer's name (order / payment / ...)
    # are already in `result`. Everything else is resolved here.
    wanted = {}
    for log in logs:
        if log.entity_id is None or log.entity in _ENTITIES:
            continue
        wanted.setdefault(log.entity, set()).add(log.entity_id)

    found = {}
    for entity, ids in wanted.items():
        lookup = _LOOKUPS.get(entity)
        found[entity] = _safe_lookup(entity, lambda: lookup(ids)) if lookup else {}

    # Names from the log rows of the same record - covers a record that no
    # longer exists (hard delete) and entity types without a lookup above.
    unresolved = {}
    for log in logs:
        if log.entity_id is None or log.entity in _ENTITIES:
            continue
        if found[log.entity].get(log.entity_id):
            continue
        name = _saved_name(log)
        if name:
            found[log.entity][log.entity_id] = name
        else:
            unresolved.setdefault(log.entity, set()).add(log.entity_id)

    if unresolved:
        from .models import AuditLog

        for entity, ids in unresolved.items():
            siblings = AuditLog.objects.filter(entity=entity, entity_id__in=ids).order_by("-created_at")
            for sib in siblings:
                if found[entity].get(sib.entity_id):
                    continue
                name = _saved_name(sib)
                if name:
                    found[entity][sib.entity_id] = name

    for log in logs:
        if log.entity_id is None or log.entity in _ENTITIES:
            continue
        name = found.get(log.entity, {}).get(log.entity_id)
        if name:
            result[(log.entity, log.entity_id)] = name

    return result