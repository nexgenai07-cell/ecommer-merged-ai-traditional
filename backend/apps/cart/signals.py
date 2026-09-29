# PATH: apps/cart/signals.py
#
# Logged-in user ka cart badalte hi uske SAB devices/tabs ko "cart_update"
# (naya item count) — e.g. phone pe item add kiya to laptop ka cart badge
# bina refresh ke badal jaye. Guest carts (session_key, user=None) ke liye
# koi WebSocket identity nahi, isliye skip.
#
# Product ka price/stock change cart page ko pehle se mil jata hai
# (public "product_update" event, apps/products/signals.py) — wo yahan
# duplicate nahi kiya.
#
# post_delete: checkout ke baad cart clear hone par bhi fire hota hai (queryset
# .delete() bhi signals bhejta hai jab receiver registered ho).

import logging

from django.db.models import Sum
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from apps.notifications.live_events import push, user_group
from .models import Cart, CartItem, Wishlist, WishlistItem

logger = logging.getLogger(__name__)


def _push_cart(cart_id):
    user_id = Cart.objects.filter(pk=cart_id).values_list("user_id", flat=True).first()
    if not user_id:
        return
    agg = CartItem.objects.filter(cart_id=cart_id).aggregate(
        lines=Sum("quantity"),
    )
    push(user_group(user_id), "cart_update", {
        "cart_id": cart_id,
        "item_count": agg["lines"] or 0,
    })


@receiver(post_save, sender=CartItem)
def cart_item_saved(sender, instance, raw=False, **kwargs):
    if raw:
        return
    try:
        _push_cart(instance.cart_id)
    except Exception:
        logger.exception("cart live push failed (cart %s)", instance.cart_id)


@receiver(post_delete, sender=CartItem)
def cart_item_deleted(sender, instance, **kwargs):
    try:
        _push_cart(instance.cart_id)
    except Exception:
        logger.exception("cart live push failed (cart %s)", instance.cart_id)


# ─────────────────────────────────────────────────────────────────────
# NEW: Wishlist live updates — cart jaisa hi. Logged-in user ki wishlist
# badalte hi uske sab devices/tabs ko "wishlist_update" (naya item count)
# jata hai. Guest wishlists (session_key, user=None) skip — unki WebSocket
# identity nahi.
# ─────────────────────────────────────────────────────────────────────

def _push_wishlist(wishlist_id):
    user_id = Wishlist.objects.filter(pk=wishlist_id).values_list("user_id", flat=True).first()
    if not user_id:
        return
    push(user_group(user_id), "wishlist_update", {
        "wishlist_id": wishlist_id,
        "item_count": WishlistItem.objects.filter(wishlist_id=wishlist_id).count(),
    })


@receiver(post_save, sender=WishlistItem)
def wishlist_item_saved(sender, instance, raw=False, **kwargs):
    if raw:
        return
    try:
        _push_wishlist(instance.wishlist_id)
    except Exception:
        logger.exception("wishlist live push failed (wishlist %s)", instance.wishlist_id)


@receiver(post_delete, sender=WishlistItem)
def wishlist_item_deleted(sender, instance, **kwargs):
    try:
        _push_wishlist(instance.wishlist_id)
    except Exception:
        logger.exception("wishlist live push failed (wishlist %s)", instance.wishlist_id)