# PATH: apps/cart/urls.py

from django.urls import path
from .views import (
    CartView, AddToCartView, UpdateCartItemView,
    RemoveCartItemView, ClearCartView, ApplyCouponView, RemoveCouponView,
)
from .wishlist_views import (
    WishlistView, AddToWishlistView, RemoveFromWishlistView,
    BulkRemoveFromWishlistView,
)

urlpatterns = [
    path('', CartView.as_view(), name='cart'),
    path('add/', AddToCartView.as_view(), name='cart-add'),
    path('update/<int:item_id>/', UpdateCartItemView.as_view(), name='cart-update'),
    path('remove/<int:item_id>/', RemoveCartItemView.as_view(), name='cart-remove'),
    path('clear/', ClearCartView.as_view(), name='cart-clear'),
    path('apply-coupon/', ApplyCouponView.as_view(), name='cart-apply-coupon'),
    path('remove-coupon/', RemoveCouponView.as_view(), name='cart-remove-coupon'),
]

wishlist_urlpatterns = [
    path('', WishlistView.as_view(), name='wishlist'),
    path('add/', AddToWishlistView.as_view(), name='wishlist-add'),
    path('remove/<int:item_id>/', RemoveFromWishlistView.as_view(), name='wishlist-remove'),
    # NEW: bulk delete — pass {"item_ids": [1,2,3]} to delete multiple
    # selected wishlist items together, in one request/one DB query.
    path('bulk-remove/', BulkRemoveFromWishlistView.as_view(), name='wishlist-bulk-remove'),
]