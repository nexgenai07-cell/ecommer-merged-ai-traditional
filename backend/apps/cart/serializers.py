# PATH: apps/cart/serializers.py

from rest_framework import serializers
from .models import Cart, CartItem
from apps.products.models import Product


class CartProductSerializer(serializers.ModelSerializer):
    """
    NEW — small nested product summary used inside cart items.
    FIX: doc documents cart items as
      { "id":1, "product": {"id":1,"name":"","price":"","primary_image":"","stock":50}, ... }
    but 'product' was previously just a raw product ID (DRF's default
    PrimaryKeyRelatedField), with name/price/image/stock scattered as
    separate flat fields (product_name, product_price, ...) instead.
    Frontend code written against the documented shape (item.product.name,
    item.product.price, etc.) would get "undefined" for all of these.

    FIX (Cross-check, Sep 2026 — PDF Part 2 Item 5): 'stock' was the
    deprecated single-field, which nothing in the codebase updates
    anymore (checkout/confirm/cancel only ever touch total_stock/
    reserved_stock now), so it was frozen/meaningless here. Spec names
    "Cart items" explicitly among the endpoints that must move to
    total_stock/reserved_stock/available_stock.
    """
    primary_image = serializers.SerializerMethodField()
    available_stock = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ['id', 'name', 'price', 'primary_image', 'total_stock', 'reserved_stock', 'available_stock']

    def get_primary_image(self, obj):
        img = obj.primary_image
        return img.image.url if img and img.image else None

    def get_available_stock(self, obj):
        return obj.total_stock - obj.reserved_stock


class CartItemSerializer(serializers.ModelSerializer):
    # FIX: 'product' is now the nested object described above, instead of
    # a bare ID.
    product = CartProductSerializer(read_only=True)
    # FIX: renamed from 'subtotal' to 'total_price' to match the documented
    # field name exactly (API 32 — Get Cart).
    total_price = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = ['id', 'product', 'quantity', 'total_price', 'created_at']

    def get_total_price(self, obj):
        return obj.product.price * obj.quantity


class CartCouponSerializer(serializers.Serializer):
    """NEW — small nested coupon summary, used instead of a bare coupon_code string."""
    code = serializers.CharField()
    type = serializers.CharField()
    value = serializers.DecimalField(max_digits=10, decimal_places=2)


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    subtotal = serializers.SerializerMethodField()
    discount_amount = serializers.SerializerMethodField()
    total = serializers.SerializerMethodField()
    # FIX: doc's Get Cart response has "coupon": null (or a coupon object
    # when applied) — the field was previously named 'coupon_code' and was
    # just a plain string, not the documented shape. Now it's a proper
    # nested object (or null when no coupon is applied).
    coupon = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = ['id', 'items', 'coupon', 'subtotal', 'discount_amount', 'total', 'created_at', 'updated_at']

    def get_subtotal(self, obj):
        return sum(item.product.price * item.quantity for item in obj.items.all())

    def get_discount_amount(self, obj):
        subtotal = self.get_subtotal(obj)
        if not obj.coupon:
            return 0
        if obj.coupon.type == 'percent':
            amount = (subtotal * obj.coupon.value) / 100
        else:
            amount = obj.coupon.value
        return min(amount, subtotal)

    def get_total(self, obj):
        return self.get_subtotal(obj) - self.get_discount_amount(obj)

    def get_coupon(self, obj):
        if not obj.coupon:
            return None
        return CartCouponSerializer(obj.coupon).data


class AddToCartSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1, default=1)

    def validate(self, data):
        try:
            product = Product.objects.get(id=data['product_id'], is_active=True)
        except Product.DoesNotExist:
            raise serializers.ValidationError({'product_id': 'Product not found.'})

        # FIX (Cross-check, Sep 2026 — PDF Part 2 Item 5): was checking
        # product.stock, the deprecated field nothing updates anymore —
        # this validation was effectively broken (comparing against a
        # frozen/stale number) for every real product. available_stock
        # (total_stock - reserved_stock) is what checkout itself checks.
        if product.available_stock < data['quantity']:
            raise serializers.ValidationError({
                'quantity': f'Only {product.available_stock} units available in stock.'
            })

        data['product'] = product
        return data


class UpdateCartItemSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=0)  # 0 means remove

    def validate_quantity(self, value):
        cart_item = self.context.get('cart_item')
        # FIX (Cross-check, Sep 2026 — PDF Part 2 Item 5): same stock ->
        # available_stock fix as AddToCartSerializer above.
        if value > 0 and cart_item and value > cart_item.product.available_stock:
            raise serializers.ValidationError(
                f'Only {cart_item.product.available_stock} units available in stock.'
            )
        return value


class ApplyCouponSerializer(serializers.Serializer):
    code = serializers.CharField()