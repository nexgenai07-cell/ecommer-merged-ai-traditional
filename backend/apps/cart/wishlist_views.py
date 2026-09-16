# PATH: apps/cart/wishlist_views.py

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


def get_or_create_wishlist(user):
    wishlist, _ = Wishlist.objects.get_or_create(user=user, defaults={'store': Store.objects.first()})
    return wishlist


class WishlistView(APIView):
    """GET /api/v1/wishlist/"""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        wishlist = get_or_create_wishlist(request.user)
        return Response(WishlistSerializer(wishlist).data)


class AddToWishlistView(APIView):
    """POST /api/v1/wishlist/add/"""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = AddToWishlistSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product_id = serializer.validated_data['product_id']

        wishlist = get_or_create_wishlist(request.user)
        WishlistItem.objects.get_or_create(wishlist=wishlist, product_id=product_id)

        return Response(WishlistSerializer(wishlist).data, status=status.HTTP_200_OK)


class RemoveFromWishlistView(APIView):
    """DELETE /api/v1/wishlist/remove/{item_id}/"""
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, item_id):
        wishlist = get_or_create_wishlist(request.user)
        try:
            item = wishlist.items.get(id=item_id)
        except WishlistItem.DoesNotExist:
            return Response({'error': 'Wishlist item not found.'}, status=status.HTTP_404_NOT_FOUND)

        item.delete()
        return Response(WishlistSerializer(wishlist).data)


# NEW: Bulk-remove endpoint — POST /api/v1/wishlist/bulk-remove/
# Body: {"item_ids": [3, 7, 12]}
#
# Deletes all matching wishlist items belonging to the logged-in user's
# own wishlist in ONE DB query (wishlist.items.filter(id__in=...).delete()),
# instead of the frontend looping and calling
# DELETE /wishlist/remove/{item_id}/ once per selected item — which is
# what was causing items to disappear one at a time instead of together.
#
# Works for both "select all and delete" (frontend just sends every
# item's id) and any partial multi-select delete.
#
# item_ids that don't exist, or belong to someone else's wishlist, are
# silently ignored (scoped via wishlist.items, never a raw WishlistItem
# query) — they never cause an error, they're just not reflected in
# removed_count.
class BulkRemoveFromWishlistView(APIView):
    """POST /api/v1/wishlist/bulk-remove/"""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = BulkRemoveFromWishlistSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item_ids = serializer.validated_data['item_ids']

        wishlist = get_or_create_wishlist(request.user)
        deleted_count, _ = wishlist.items.filter(id__in=item_ids).delete()

        return Response(
            {
                'message': 'Wishlist items removed.',
                'removed_count': deleted_count,
                'wishlist': WishlistSerializer(wishlist).data,
            },
            status=status.HTTP_200_OK,
        )