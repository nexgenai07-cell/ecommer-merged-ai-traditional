# PATH: apps/cart/serializers.py

from rest_framework import serializers
from .models import Cart, CartItem
from apps.products.models import Product, ProductVariant


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


class CartVariantSerializer(serializers.ModelSerializer):
    """
    NEW (Oct 2026 — product variants): small nested summary of the variant
    (color / size-kit) the customer picked, inside a cart item.
    """
    label = serializers.CharField(read_only=True)
    available_stock = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = ['id', 'color', 'color_hex', 'size', 'label', 'price', 'available_stock']

    def get_available_stock(self, obj):
        return obj.total_stock - obj.reserved_stock


class CartItemSerializer(serializers.ModelSerializer):
    # FIX: 'product' is now the nested object described above, instead of
    # a bare ID.
    product = CartProductSerializer(read_only=True)
    # NEW (Oct 2026 — product variants): null for a product without
    # variants. For a variant item, use unit_price / available_stock below
    # rather than product.price / product.available_stock (those belong
    # to the product as a whole).
    variant = CartVariantSerializer(read_only=True)
    unit_price = serializers.SerializerMethodField()
    available_stock = serializers.SerializerMethodField()
    is_available = serializers.SerializerMethodField()
    # FIX: renamed from 'subtotal' to 'total_price' to match the documented
    # field name exactly (API 32 — Get Cart).
    total_price = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = [
            'id', 'product', 'variant', 'quantity',
            'unit_price', 'total_price',
            'available_stock', 'is_available',
            'created_at',
        ]

    # UPDATED (Oct 2026 — product variants): price of the picked variant
    # when there is one, otherwise the product's price (see
    # CartItem.unit_price / total_price in models.py).
    def get_unit_price(self, obj):
        return obj.unit_price

    def get_total_price(self, obj):
        return obj.total_price

    def get_available_stock(self, obj):
        return obj.available_stock

    def get_is_available(self, obj):
        return obj.is_available


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
        # UPDATED (Oct 2026 — product variants): item.total_price uses the
        # variant's price when one was picked.
        return sum(item.total_price for item in obj.items.all())

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
    # NEW (Oct 2026 — product variants): compulsory when the product has
    # variants (color / size-kit), must be left out for a product that
    # has none.
    variant_id = serializers.IntegerField(required=False, allow_null=True)
    quantity = serializers.IntegerField(min_value=1, default=1)

    def validate(self, data):
        try:
            product = Product.objects.get(
                id=data['product_id'], is_active=True, is_delete=False
            )
        except Product.DoesNotExist:
            raise serializers.ValidationError({'product_id': 'Product not found.'})

        variant_id = data.get('variant_id')
        variant = None

        if product.has_variants:
            if variant_id is None:
                raise serializers.ValidationError({
                    'variant_id': 'Please choose a variant (color / size) for this product.'
                })
            try:
                variant = product.variants.get(
                    id=variant_id, is_active=True, is_delete=False
                )
            except ProductVariant.DoesNotExist:
                raise serializers.ValidationError({
                    'variant_id': 'This variant was not found or is no longer available.'
                })
            available_stock = variant.available_stock
        else:
            if variant_id is not None:
                raise serializers.ValidationError({
                    'variant_id': 'This product has no variants.'
                })
            # FIX (Cross-check, Sep 2026 — PDF Part 2 Item 5): was checking
            # product.stock, the deprecated field nothing updates anymore —
            # this validation was effectively broken (comparing against a
            # frozen/stale number) for every real product. available_stock
            # (total_stock - reserved_stock) is what checkout itself checks.
            available_stock = product.available_stock

        if available_stock < data['quantity']:
            raise serializers.ValidationError({
                'quantity': f'Only {available_stock} units available in stock.'
            })

        data['product'] = product
        data['variant'] = variant
        return data


class UpdateCartItemSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=0)  # 0 means remove

    def validate_quantity(self, value):
        cart_item = self.context.get('cart_item')

        # NEW (Oct 2026 — product variants): the picked variant was
        # deactivated / deleted after it was added — the customer can
        # still remove it (quantity 0) but not raise or keep a quantity.
        if (
            value > 0
            and cart_item
            and cart_item.variant_id
            and not (cart_item.variant.is_active and not cart_item.variant.is_delete)
        ):
            raise serializers.ValidationError(
                'This variant is no longer available. Please remove it from your cart.'
            )

        # FIX (Cross-check, Sep 2026 — PDF Part 2 Item 5): same stock ->
        # available_stock fix as AddToCartSerializer above.
        # UPDATED (Oct 2026 — product variants): cart_item.available_stock
        # is the variant's stock for a variant item, the product's
        # otherwise.
        if value > 0 and cart_item and value > cart_item.available_stock:
            raise serializers.ValidationError(
                f'Only {cart_item.available_stock} units available in stock.'
            )
        return value


class ApplyCouponSerializer(serializers.Serializer):
    code = serializers.CharField()