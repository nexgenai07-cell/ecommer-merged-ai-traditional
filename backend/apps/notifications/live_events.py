# PATH: apps/notifications/live_events.py
#
# Live-update ka SINGLE helper. Kisi bhi app se real-time event bhejna ho
# to sirf push(...) call karo. Ye kabhi exception raise nahi karta: agar
# Redis/Upstash down ya slow ho to bhi asli save/checkout kabhi fail
# nahi hoga (live update ek side-effect hai, same philosophy jo
# create_notification() ki hai).
#
# GROUPS (naam existing chat groups "chat_*" / "admin_chat_*" se conflict nahi karte):
#   live_public              -> har connected client (products ka price/stock)
#   live_user_<user_id>      -> sirf us user ke liye (uske orders, payments, notifications)
#   live_admin_store_<id>    -> sirf us store ke admins ke liye (naye orders, order/payment changes)

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction

logger = logging.getLogger(__name__)

PUBLIC_GROUP = "live_public"


def user_group(user_id):
    return f"live_user_{user_id}"


def admin_store_group(store_id):
    return f"live_admin_store_{store_id}"


def _send(group, event, data):
    try:
        layer = get_channel_layer()
        if layer is None:
            return
        async_to_sync(layer.group_send)(
            group,
            {"type": "live_event", "event": event, "data": data},
        )
    except Exception:
        logger.exception("live push failed (group=%s, event=%s)", group, event)


def push(group, event, data):
    """DB transaction commit hone ke BAAD hi message bhejta hai (agar
    transaction rollback ho jaye to customer ko jhooti update nahi jati).
    Transaction ke bahar ho to foran bhej deta hai."""
    transaction.on_commit(lambda: _send(group, event, data))
