# PATH: apps/categories/signals.py
#
# Category add / edit / soft-delete / hide -> sab visitors ko live update
# (navbar / category menu bina refresh ke). Delete yahan soft-delete hai
# (is_delete=True + .save(update_fields=...)), isliye post_save kafi hai.
#
# Public group hai — sirf wahi fields jo customers ko waise bhi dikhti hain.

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.notifications.live_events import push, PUBLIC_GROUP
from .models import Category

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Category)
def category_saved(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    try:
        push(PUBLIC_GROUP, "category_update", {
            "id": instance.id,
            "store_id": instance.store_id,
            "name": instance.name,
            "is_active": instance.is_active,
            "is_delete": instance.is_delete,
            "created": created,
        })
    except Exception:
        logger.exception("category live push failed for category %s", getattr(instance, "id", None))
