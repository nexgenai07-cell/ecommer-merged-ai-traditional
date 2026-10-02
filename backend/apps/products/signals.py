# PATH: apps/products/signals.py
#
# Product ka koi bhi .save() (admin panel, API, checkout ka stock reserve,
# order cancel pe stock release) -> customers ko live price/stock update.
#
# NOTE: .update() / F() expressions signals fire NAHI karte. Isi liye
# services.adjust_stock() (jo .update(F(...)) use karta hai) broadcast_product()
# ko khud call karta hai.
#
# UPDATED (Oct 2026 - new product / category change not showing on the
# customer's category filter without a refresh): the event used to carry
# only price / stock, so a product that was NOT already on the customer's
# screen (a brand-new product, or one moved into the category being
# viewed) gave the frontend nothing to decide with. It now also carries
#   created              True when the product was just added
#   category_id          the product's category now
#   previous_category_id the category it was in before this save (only set
#                        when the category really changed, else None)
# so the frontend can refetch the list when category_id matches the
# category filter on screen, and drop the product from the list when
# previous_category_id matches it. Event name is unchanged (product_update).
#
# SECURITY: ye group public hai (anonymous bhi sunta hai), isliye yahan
# purchase_price jaisa admin-only field KABHI nahi bhejna.

import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.notifications.live_events import push, PUBLIC_GROUP
from .models import Discount, Product

logger = logging.getLogger(__name__)


def product_payload(product, created=False):
    return {
        "id": product.id,
        "store_id": product.store_id,
        "name": product.name,
        "category_id": product.category_id,
        "previous_category_id": getattr(product, "_previous_category_id", None),
        "created": created,
        "price": str(product.price),
        "original_price": (
            str(product.original_price) if product.original_price is not None else None
        ),
        "total_stock": product.total_stock,
        "reserved_stock": product.reserved_stock,
        "available_stock": max(product.total_stock - product.reserved_stock, 0),
        "is_active": product.is_active,
        "is_delete": product.is_delete,
    }


def broadcast_product(product, created=False):
    try:
        push(PUBLIC_GROUP, "product_update", product_payload(product, created))
    except Exception:
        logger.exception("broadcast_product failed for product %s", getattr(product, "id", None))


# Remembers the category the product had BEFORE this save, so a category
# change can be told to customers who are looking at the old category.
# The extra query only runs when the category could actually change
# (full save, or update_fields that include the category) - the frequent
# stock-only saves (checkout, cancel) skip it.
@receiver(pre_save, sender=Product)
def product_category_before_save(sender, instance, raw=False, update_fields=None, **kwargs):
    instance._previous_category_id = None
    if raw or instance.pk is None:
        return
    if update_fields is not None and not {"category", "category_id"} & set(update_fields):
        return
    try:
        old = (
            Product.objects.filter(pk=instance.pk)
            .values_list("category_id", flat=True)
            .first()
        )
        if old is not None and old != instance.category_id:
            instance._previous_category_id = old
    except Exception:
        logger.exception("could not read previous category for product %s", instance.pk)


@receiver(post_save, sender=Product)
def product_saved(sender, instance, created, raw=False, **kwargs):
    if raw:  # loaddata/fixtures
        return
    broadcast_product(instance, created=created)


# ------------------------------------------------------------------
# Coupon / discount live update (Oct 2026)
#
# An admin edits, deactivates or deletes a coupon while a customer is
# already on the cart / checkout page with it applied -> the customer's
# screen kept showing the OLD discount until a refresh, and the order was
# then placed with a different amount than the one they had seen.
#
# Sent ONLY to the customers whose cart currently has this coupon applied
# (their private group live_user_<id>), NOT to the public group: coupon
# codes are not public, so they must not be broadcast to every visitor.
# Event name: "coupon_update". On receiving it the frontend should reload
# the cart (GET /cart/) and show the new total.
# ------------------------------------------------------------------
def coupon_payload(discount):
    from django.utils import timezone

    now = timezone.now()
    return {
        "discount_id": discount.id,
        "code": discount.code,
        "type": discount.type,
        "value": str(discount.value),
        "min_order_amount": (
            str(discount.min_order_amount)
            if discount.min_order_amount is not None else None
        ),
        "start_date": discount.start_date.isoformat() if discount.start_date else None,
        "end_date": discount.end_date.isoformat() if discount.end_date else None,
        "is_active": discount.is_active,
        "is_delete": discount.is_delete,
        "is_valid": bool(
            discount.is_active
            and not discount.is_delete
            and discount.start_date <= now <= discount.end_date
        ),
    }


@receiver(post_save, sender=Discount)
def discount_saved(sender, instance, created, raw=False, **kwargs):
    if raw or created:  # a brand-new coupon cannot be on any cart yet
        return
    try:
        from apps.cart.models import Cart
        from apps.notifications.live_events import user_group

        user_ids = set(
            Cart.objects.filter(coupon=instance, user__isnull=False)
            .values_list("user_id", flat=True)
        )
        if not user_ids:
            return
        payload = coupon_payload(instance)
        for user_id in user_ids:
            push(user_group(user_id), "coupon_update", payload)
    except Exception:
        logger.exception("coupon live push failed for discount %s", getattr(instance, "id", None))