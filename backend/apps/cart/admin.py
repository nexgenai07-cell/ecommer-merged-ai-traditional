from django.contrib import admin
from .models import Wishlist, WishlistItem


@admin.register(Wishlist)
class WishlistAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'user',
        'session_key',
        'created_at',
    ]


@admin.register(WishlistItem)
class WishlistItemAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'wishlist',
        'product',
        'created_at',
    ]