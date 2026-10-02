# PATH: apps/orders/signals.py
#
# Order / Payment ka har .save() -> live event:
#   * customer ko (sirf usi ko)            -> group live_user_<id>
#   * us store ke admins ko                -> group live_admin_store_<id>
# Guest orders (customer.user = None) ke liye customer-side push skip hota hai
# (unka koi login/WebSocket identity nahi), admin-side phir bhi jata hai.
#
# UPDATED (Oct 2026 - admin Customers page needed a refresh): every
# Customer row insert/save now also sends "customer_update" to
# live_admin_store_<id> (admins only - the payload carries the phone, so it
# must never go to the public group). Covers a new registration (a Customer
# row is created with the account) and checkout "Save Address" phone edits.
# Profile name/phone edits go through apps/users/customer_sync.py, which
# uses queryset.update() (no post_save), so it calls
# broadcast_customer() itself.
#
# Covered automatically (sab .save() karte hain): admin status update,
# admin bulk status update, customer cancel, cancel_stale_payments scheduler,
# checkout, Stripe webhook, QR proof upload / approve / reject / extend.
#
# UPDATED (Sep 2026 — dashboard live): admin dashboard 5 minute cache hota
# hai aur uski key "analytics_dashboard_v2" hai, jabke orders/views.py aur
# payments/views.py "analytics_dashboard" delete karte hain (purani key) —
# yani invalidate kabhi actual cache tak pahunchta hi nahi tha. Yahan dono
# keys delete hoti hain, phir admins ko "dashboard_update" jata hai taake
# frontend dashboard dobara fetch kare (fresh numbers, bina refresh).

import logging

from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.notifications.live_events import push, user_group, admin_store_group
from .models import Customer, Order, Payment
from .customer_contact import resolve_customer_name, resolve_customer_phone

logger = logging.getLogger(__name__)

DASHBOARD_CACHE_KEYS = ("analytics_dashboard", "analytics_dashboard_v2")


def order_payload(order):
    return {
        "id": order.id,
        "order_number": order.order_number,
        "status": order.status,
        "status_display": order.get_status_display(),
        "total_amount": str(order.total_amount),
        "tracking_number": order.tracking_number or "",
        "store_id": order.store_id,
    }


def _invalidate_dashboard_cache():
    try:
        cache.delete_many(DASHBOARD_CACHE_KEYS)
    except Exception:
        logger.exception("dashboard cache invalidation failed")


def _refresh_dashboard(store_id):
    # Cache DB commit ke BAAD delete hota hai. Pehle delete karte to commit se
    # pehle aayi koi dashboard request purana data dobara 5 minute ke liye
    # cache kar deti.
    transaction.on_commit(_invalidate_dashboard_cache)
    push(admin_store_group(store_id), "dashboard_update", {"store_id": store_id})


@receiver(post_save, sender=Order)
def order_saved(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    try:
        data = order_payload(instance)
        admin_group = admin_store_group(instance.store_id)

        if created:
            push(admin_group, "new_order", data)
        else:
            push(admin_group, "order_update", data)

        user_id = instance.customer.user_id
        if user_id:
            push(user_group(user_id), "order_update", data)

        _refresh_dashboard(instance.store_id)
    except Exception:
        logger.exception("order live push failed for order %s", getattr(instance, "id", None))


@receiver(post_save, sender=Payment)
def payment_saved(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    try:
        order = instance.order
        data = {
            "order_id": order.id,
            "order_number": order.order_number,
            "payment_status": instance.status,
            "payment_method": instance.payment_method,
            "store_id": order.store_id,
        }
        push(admin_store_group(order.store_id), "payment_update", data)

        user_id = order.customer.user_id
        if user_id:
            push(user_group(user_id), "payment_update", data)

        # Checkout mein Order aur Payment saath bante hain: Order ka signal
        # dashboard_update pehle hi bhej chuka hota hai, dobara nahi.
        if not created:
            _refresh_dashboard(order.store_id)
    except Exception:
        logger.exception("payment live push failed for payment %s", getattr(instance, "id", None))


# ------------------------------------------------------------------
# Customers page live update (admins only)
# ------------------------------------------------------------------
def customer_payload(customer, created=False):
    """Same name / phone the admin Customers list and CSV export show
    (see customer_contact.py)."""
    user = getattr(customer, "user", None)
    return {
        "id": customer.id,
        "user_id": customer.user_id,
        "store_id": customer.store_id,
        "name": resolve_customer_name(customer),
        "phone": resolve_customer_phone(customer),
        "email": (getattr(user, "email", None) or customer.email or ""),
        "created": created,
    }


def broadcast_customer(customer, created=False):
    try:
        push(
            admin_store_group(customer.store_id),
            "customer_update",
            customer_payload(customer, created),
        )
    except Exception:
        logger.exception("customer live push failed for customer %s", getattr(customer, "id", None))


@receiver(post_save, sender=Customer)
def customer_saved(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    broadcast_customer(instance, created)