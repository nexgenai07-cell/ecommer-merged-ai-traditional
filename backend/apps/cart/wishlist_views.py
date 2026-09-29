# PATH: apps/cart/wishlist_views.py
#
# Wishlist ab cart jaisa kaam karta hai:
#   1. Guest (bina login) bhi wishlist use kar sakta hai — X-Cart-Session
#      (ya purana X-Session-Key) header se pehchaan, cart wali hi key.
#   2. Guest ke responses mein "session_key" wapas aati hai taake frontend
#      usay localStorage mein rakh sake.
#   3. Login par guest wishlist user ki wishlist mein merge hoti hai
#      (cart ke merge ke saath hi, merge_guest_cart_into_user_cart se).
#   4. Live update: user ki wishlist badalte hi "wishlist_update" event
#      (apps/cart/signals.py).
#   5. clear/ endpoint (cart/clear/ ki tarah).
#
# Response shapes purani hi hain (WishlistSerializer ka data) — sirf naye
# keys add hue hain (message, session_key, ...), isliye purana frontend
# code toot'ta nahi.

import logging
import uuid

from django.db import transaction
from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response

from .models import Wishlist, WishlistItem
from .wishlist_serializers import (
    WishlistSerializer,
    AddToWishlistSerializer,
    BulkRemoveFromWishlistSerializer,
)
from apps.stores.models import Store

logger = logging.getLogger(__name__)


def _get_guest_session_key(request):
    # Same headers as the cart: X-Cart-Session, with X-Session-Key kept
    # for backward compatibility.
    return (
        request.headers.get("X-Cart-Session")
        or request.headers.get("X-Session-Key")
    )


def get_or_create_wishlist(user):
    """Kept for backward compatibility (logged-in user only)."""
    wishlist, _ = Wishlist.objects.get_or_create(
        user=user,
        defaults={'store': Store.objects.first()},
    )
    return wishlist


def get_or_create_wishlist_for_request(request):
    """
    Returns:
        (wishlist, session_key, is_new_session)

    Authenticated:
        Uses the user's account wishlist.

    Anonymous:
        Uses X-Cart-Session when provided.
        If no session key is provided, generates a new one.
    """
    store = Store.objects.first()

    # Authenticated user -> account wishlist
    if request.user and request.user.is_authenticated:
        wishlist, _ = Wishlist.objects.get_or_create(
            user=request.user,
            store=store,
        )
        return wishlist, None, False

    session_key = _get_guest_session_key(request)
    is_new_session = False

    if not session_key:
        session_key = uuid.uuid4().hex
        is_new_session = True

    wishlist, created = Wishlist.objects.get_or_create(
        session_key=session_key,
        user=None,
        store=store,
    )

    # A key was supplied but no wishlist existed for it yet:
    # this is still the first wishlist request of that guest session.
    if created:
        is_new_session = True

    return wishlist, session_key, is_new_session


def _with_session_key(data, session_key):
    """Adds session_key to a response dict for guests (like the cart does)."""
    if session_key:
        data["session_key"] = session_key
    return data


def merge_guest_wishlist_into_user_wishlist(request, user):
    """
    Merge the guest wishlist identified by X-Cart-Session into the
    authenticated user's account wishlist.

    Products already in the account wishlist are not duplicated.
    Products that are inactive/deleted are skipped.

    Returns True if a guest wishlist was found and merged.
    Called from merge_guest_cart_into_user_cart (views.py), i.e. at login.
    """
    session_key = _get_guest_session_key(request)

    if not session_key:
        return False

    store = Store.objects.first()

    if not store:
        return False

    try:
        guest_wishlist = Wishlist.objects.get(
            session_key=session_key,
            user__isnull=True,
            store=store,
        )
    except Wishlist.DoesNotExist:
        return False

    new_items = []

    with transaction.atomic():
        account_wishlist, _ = Wishlist.objects.get_or_create(
            user=user,
            store=store,
        )

        existing_product_ids = set(
            account_wishlist.items.values_list("product_id", flat=True)
        )

        guest_product_ids = guest_wishlist.items.filter(
            product__is_active=True,
            product__is_delete=False,
        ).values_list("product_id", flat=True)

        new_items = [
            WishlistItem(wishlist=account_wishlist, product_id=product_id)
            for product_id in guest_product_ids
            if product_id not in existing_product_ids
        ]

        WishlistItem.objects.bulk_create(new_items, ignore_conflicts=True)

        # Guest wishlist has been processed — remove it so it cannot be
        # merged a second time.
        guest_wishlist.delete()

    # bulk_create does not fire post_save, so tell the user's other
    # devices/tabs about the new count once, manually.
    if new_items:
        try:
            from .signals import _push_wishlist
            _push_wishlist(account_wishlist.id)
        except Exception:
            logger.exception(
                "wishlist live push failed after merge (wishlist %s)",
                account_wishlist.id,
            )

    return True


class WishlistView(APIView):
    """GET /api/v1/wishlist/"""
    # AllowAny — guests can see their wishlist too (via session key)
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        wishlist, session_key, is_new_session = get_or_create_wishlist_for_request(request)

        data = WishlistSerializer(wishlist).data

        # Guest session key must be returned so the frontend can store it
        # in localStorage and send it on future requests.
        _with_session_key(data, session_key)

        return Response(data)


class AddToWishlistView(APIView):
    """POST /api/v1/wishlist/add/"""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = AddToWishlistSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product_id = serializer.validated_data['product_id']

        wishlist, session_key, is_new_session = get_or_create_wishlist_for_request(request)

        _, created = WishlistItem.objects.get_or_create(
            wishlist=wishlist,
            product_id=product_id,
        )

        data = WishlistSerializer(wishlist).data
        data['message'] = (
            'Product added to wishlist.'
            if created
            else 'Product is already in your wishlist.'
        )
        data['wishlist_total_items'] = wishlist.items.count()

        _with_session_key(data, session_key)

        return Response(data, status=status.HTTP_200_OK)


class RemoveFromWishlistView(APIView):
    """DELETE /api/v1/wishlist/remove/{item_id}/"""
    permission_classes = [permissions.AllowAny]

    def delete(self, request, item_id):
        wishlist, session_key, is_new_session = get_or_create_wishlist_for_request(request)

        try:
            item = wishlist.items.get(id=item_id)
        except WishlistItem.DoesNotExist:
            return Response(
                _with_session_key({'error': 'Wishlist item not found.'}, session_key),
                status=status.HTTP_404_NOT_FOUND,
            )

        item.delete()

        data = WishlistSerializer(wishlist).data
        data['message'] = 'Item removed from wishlist.'

        _with_session_key(data, session_key)

        return Response(data, status=status.HTTP_200_OK)


# Bulk-remove endpoint — POST /api/v1/wishlist/bulk-remove/
# Body: {"item_ids": [3, 7, 12]}
#
# Deletes all matching wishlist items belonging to the caller's own
# wishlist (account wishlist, or guest wishlist for the given session key)
# in ONE DB query (wishlist.items.filter(id__in=...).delete()), instead of
# the frontend looping and calling DELETE /wishlist/remove/{item_id}/ once
# per selected item.
#
# item_ids that don't exist, or belong to someone else's wishlist, are
# silently ignored (scoped via wishlist.items, never a raw WishlistItem
# query) — they never cause an error, they're just not reflected in
# removed_count.
class BulkRemoveFromWishlistView(APIView):
    """POST /api/v1/wishlist/bulk-remove/"""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = BulkRemoveFromWishlistSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item_ids = serializer.validated_data['item_ids']

        wishlist, session_key, is_new_session = get_or_create_wishlist_for_request(request)
        deleted_count, _ = wishlist.items.filter(id__in=item_ids).delete()

        response_data = {
            'message': 'Wishlist items removed.',
            'removed_count': deleted_count,
            'wishlist': WishlistSerializer(wishlist).data,
        }

        _with_session_key(response_data, session_key)

        return Response(response_data, status=status.HTTP_200_OK)


class ClearWishlistView(APIView):
    """DELETE /api/v1/wishlist/clear/"""
    permission_classes = [permissions.AllowAny]

    def delete(self, request):
        wishlist, session_key, is_new_session = get_or_create_wishlist_for_request(request)

        wishlist.items.all().delete()

        response_data = {
            'message': 'Wishlist cleared.'
        }

        _with_session_key(response_data, session_key)

        return Response(response_data, status=status.HTTP_200_OK)