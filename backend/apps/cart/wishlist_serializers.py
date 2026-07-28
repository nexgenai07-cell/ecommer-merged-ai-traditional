# PATH: apps/cart/wishlist_serializers.py

from rest_framework import serializers
from .models import Wishlist, WishlistItem
from apps.products.models import Product


class WishlistProductSerializer(serializers.ModelSerializer):
    primary_image = serializers.SerializerMethodField()
    in_stock = serializers.ReadOnlyField()
    # FIX: 'category' was returning just the raw category ID (e.g. 3) instead
    # of its name. Frontend does product.category?.name, which needs an
    # object/string with a .name — StringRelatedField sends the category's
    # __str__ (its name) as a plain string instead of the numeric ID.
    category = serializers.SerializerMethodField()

    def get_category(self, obj):
        if obj.category:
            return {"id": obj.category.id, "name": obj.category.name}
        return None

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "price",
            "original_price",  # FIX: was missing entirely — frontend needs
                                # this for the strikethrough price + discount
                                # badge (PriceDisplay component).
            "primary_image",
            "in_stock",
            "stock",
            "category",
        ]

    def get_primary_image(self, obj):
        image = obj.primary_image
        if image and image.image:
            return image.image.url
        return None


class WishlistItemSerializer(serializers.ModelSerializer):
    product = WishlistProductSerializer(read_only=True)

    class Meta:
        model = WishlistItem
        fields = [
            "id",
            "product",
            "created_at",
        ]


class WishlistSerializer(serializers.ModelSerializer):
    items = WishlistItemSerializer(many=True, read_only=True)

    class Meta:
        model = Wishlist
        fields = [
            "id",
            "items",
            "created_at",
        ]


class AddToWishlistSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()

    def validate_product_id(self, value):
        if not Product.objects.filter(
    id=value,
    is_active=True,
    is_delete=False,
     ).exists():
            raise serializers.ValidationError("Product not found.")
        return value