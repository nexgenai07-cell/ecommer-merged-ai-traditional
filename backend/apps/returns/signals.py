# PATH: apps/returns/signals.py
#
# Return aur Complaint ka status live:
#   * customer ko (sirf usi ko)  -> return_update / complaint_update
#   * us store ke admins ko      -> new_return / new_complaint (naya bane to),
#                                   warna return_update / complaint_update
#
# NOTE: complaint CHAT ke messages ka apna alag WebSocket pehle se hai
# (apps/returns/consumers.py, ws/complaints/<id>/). Ye signals us se
# alag hain aur usse touch nahi karte — ye sirf STATUS changes ke liye hain
# (open -> in_progress -> resolved, pending -> approved/rejected), taake list
# pages bina refresh ke badlein.

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.notifications.live_events import push, user_group, admin_store_group
from .models import Return, Complaint

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Return)
def return_saved(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    try:
        order = instance.order
        data = {
            "id": instance.id,
            "order_id": order.id,
            "order_number": order.order_number,
            "status": instance.status,
            "status_display": instance.get_status_display(),
            "store_id": order.store_id,
        }
        push(admin_store_group(order.store_id), "new_return" if created else "return_update", data)

        customer = instance.customer or order.customer
        if customer and customer.user_id:
            push(user_group(customer.user_id), "return_update", data)
    except Exception:
        logger.exception("return live push failed for return %s", getattr(instance, "id", None))


@receiver(post_save, sender=Complaint)
def complaint_saved(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    try:
        customer = instance.customer
        data = {
            "id": instance.id,
            "status": instance.status,
            "status_display": instance.get_status_display(),
            "priority": instance.priority,
            "type": instance.type,
            "order_id": instance.order_id,
            "store_id": customer.store_id,
        }
        push(admin_store_group(customer.store_id), "new_complaint" if created else "complaint_update", data)

        if customer.user_id:
            push(user_group(customer.user_id), "complaint_update", data)
    except Exception:
        logger.exception("complaint live push failed for complaint %s", getattr(instance, "id", None))
