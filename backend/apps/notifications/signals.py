# PATH: apps/notifications/signals.py
#
# Har NAYI Notification row -> us user ko live "notification" event (bell/toast
# bina refresh ke). Signal isliye, helper create_notification() mein nahi:
# apps/returns/views.py 4 jagah Notification.objects.create() seedha karta hai
# (complaint reply notifications) — helper mein push lagane se wo miss ho jati.
# Signal har raste ko cover karta hai.
#
# Sirf created=True: "mark as read" bhi .save() hai, uspe dobara toast
# nahi aana chahiye.
# user=None (broadcast) notifications yahan skip hoti hain — unka koi ek
# recipient nahi, aur public group mein bhejna private content leak kar sakta hai.

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from .live_events import push, user_group
from .models import Notification
from .customer_names import customer_names_for

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Notification)
def notification_created(sender, instance, created, raw=False, **kwargs):
    if raw or not created or not instance.user_id:
        return
    try:
        # NEW: admins also get the customer's name in the live event (same
        # value the notification list returns as customer_name).
        customer_name = None
        recipient = instance.user
        if getattr(recipient, "role", None) != "customer":
            customer_name = customer_names_for([instance]).get(
                (instance.reference_type, str(instance.reference_id))
            )

        push(user_group(instance.user_id), "notification", {
            "id": instance.id,
            "title": instance.title,
            "message": instance.message,
            "type": instance.type,
            "reference_type": instance.reference_type,
            "reference_id": instance.reference_id,
            "is_read": instance.is_read,
            "created_at": instance.created_at.isoformat(),
            "customer_name": customer_name,
        })
    except Exception:
        logger.exception("notification live push failed for notification %s", getattr(instance, "id", None))
