# PATH: apps/products/signals.py
#
# Product ka koi bhi .save() (admin panel, API, checkout ka stock reserve,
# order cancel pe stock release) -> customers ko live price/stock update.
#
# NOTE: .update() / F() expressions signals fire NAHI karte. Isi liye
# services.adjust_stock() (jo .update(F(...)) use karta hai) broadcast_product()
# ko khud call karta hai.
#
# SECURITY: ye group public hai (anonymous bhi sunta hai), isliye yahan
# purchase_price jaisa admin-only field KABHI nahi bhejna.

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.notifications.live_events import push, PUBLIC_GROUP
from .models import Product

logger = logging.getLogger(__name__)


def product_payload(product):
    return {
        "id": product.id,
        "store_id": product.store_id,
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


def broadcast_product(product):
    try:
        push(PUBLIC_GROUP, "product_update", product_payload(product))
    except Exception:
        logger.exception("broadcast_product failed for product %s", getattr(product, "id", None))


@receiver(post_save, sender=Product)
def product_saved(sender, instance, created, raw=False, **kwargs):
    if raw:  # loaddata/fixtures
        return
    broadcast_product(instance)
