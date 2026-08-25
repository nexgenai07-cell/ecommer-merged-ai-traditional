# PATH: apps/orders/serializers.py

import re

from rest_framework import serializers
from .models import Customer, Order, OrderItem, Payment

# Pakistani mobile/landline numbers: optional +92 or leading 0, then 9-11
# digits. Kept permissive on purpose (spaces/dashes stripped before check)
# so real numbers aren't rejected, but garbage input is (B15).
PHONE_RE = re.compile(r'^(\+92|0)\d{9,10}$')

# Pakistan Post uses 5-digit postal codes. Field stays optional (B18) —
# this only runs when the customer actually typed something in.
POSTAL_CODE_RE = re.compile(r'^\d{4,6}$')

# Converts each order item into API response format.
# Used inside OrderDetailSerializer.
class OrderItemSerializer(serializers.ModelSerializer):
    # FIX (B25): product image was completely missing from order items, so
    # the customer had no way to identify what they ordered from the order
    # detail screen. Uses the same primary-image lookup pattern already
    # used in products/serializers.py (ProductListSerializer.get_primary_image)
    # so behaviour stays consistent across the app.
    product_image = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "product",
            "product_name",
            "product_image",
            "price",
            "quantity",
            "total_price",
        ]

    def get_product_image(self, obj):
        product = obj.product
        # product can be None — OrderItem.product is SET_NULL if the
        # product was later deleted, but the order should still render.
        if not product:
            return None

        img = product.images.filter(is_primary=True).first() or product.images.first()
        if not img or not img.image:
            return None

        image = img.image
        if hasattr(image, "url"):
            return image.url.replace("http://", "https://")
        return str(image)

# Converts payment details into API response.
# Used when returning complete order information.
class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            "id",
            "stripe_payment_intent_id",
            "status",
            "amount",
            "paid_at",
        ]

# Returns a lightweight order summary for customer order history.
class OrderListSerializer(serializers.ModelSerializer):
    """Lightweight — used for order history list (My Orders, API 53)"""

    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "total_amount",
            "discount_amount",
            "status",
            "item_count",
            "created_at",
        ]
        
# Counts how many products belong to this order.
    def get_item_count(self, obj):
        return obj.items.count()

# Returns order summary with customer information for admin dashboard.
class AdminOrderListSerializer(serializers.ModelSerializer):
    """
    Used for Admin Order List and Admin Order Filter APIs.
    """

    customer = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "customer",
            "total_amount",
            "discount_amount",
            "status",
            "created_at",
        ]

# Formats customer details into a small nested object.
    def get_customer(self, obj):
        return {
            "id": obj.customer.id,
            "name": obj.customer.name,
            "phone": obj.customer.phone,
        }

# Returns complete order details including customer, items and payment.
class OrderDetailSerializer(serializers.ModelSerializer):
    customer = serializers.SerializerMethodField()

    items = OrderItemSerializer(
        many=True,
        read_only=True,
    )

    payment = PaymentSerializer(
        read_only=True,
    )

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "status",
            "total_amount",
            "discount_amount",
            "shipping_address",
            "city",
            "postal_code",
            "contact_phone",
            "tracking_number",
            "notes",
            "created_at",
            "updated_at",

            "customer",

            "items",
            "payment",
        ]

# Returns complete customer information for the order.
    def get_customer(self, obj):
        return {
            "id": obj.customer.id,
            "name": obj.customer.name,
            "email": obj.customer.email,
            "phone": obj.customer.phone,
        }

# Validates checkout request before creating an order.
class CheckoutSerializer(serializers.Serializer):
    """POST /api/v1/orders/checkout/

    FIX (B15/B18/B19/B22/F8): previously this only had shipping_address +
    notes, so city/postal_code/phone had nowhere to go — the frontend could
    send them but the backend silently dropped them, and there was zero
    validation on any of it. Every field here is required=False because the
    view falls back to the customer's last saved profile (Customer.city /
    Customer.postal_code / Customer.phone) when a field is omitted — that's
    what makes prefill (F8) and "returning customer doesn't retype
    everything" actually work. shipping_address and city are the only two
    that are truly mandatory to place an order, and that's enforced in
    CheckoutView once the fallback has been applied, not here.
    """

    shipping_address = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=500,
    )
    city = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
    )
    # FIX (B18): explicitly optional — checkout must not block on this.
    postal_code = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=20,
    )
    # FIX (B15): validated contact number, separate from account phone.
    phone = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=20,
    )
    # FIX (B22): when true, whatever address/city/postal_code/phone was
    # used for this order also gets written back onto the Customer profile.
    save_address = serializers.BooleanField(required=False, default=False)

    notes = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    # FIX (B19): city/address get real validation instead of none.
    def validate_shipping_address(self, value):
        value = value.strip()
        if value and len(value) < 8:
            raise serializers.ValidationError(
                "Shipping address looks too short — please enter a full address."
            )
        return value

    def validate_city(self, value):
        value = value.strip()
        if value and any(ch.isdigit() for ch in value):
            raise serializers.ValidationError(
                "City name should not contain numbers."
            )
        return value

    # FIX (B18/B19): postal code is optional, but if the customer types
    # something in, it has to actually look like a postal code.
    def validate_postal_code(self, value):
        value = value.strip()
        if value and not POSTAL_CODE_RE.match(value):
            raise serializers.ValidationError(
                "Postal code should be 4-6 digits (leave blank if unknown)."
            )
        return value

    # FIX (B15): this is the "number field bug during checkout" — there
    # was no server-side validation at all before, so malformed numbers
    # (letters, wrong length, missing country/area code) were accepted and
    # silently stored.
    def validate_phone(self, value):
        cleaned = re.sub(r'[\s-]', '', value)
        if cleaned and not PHONE_RE.match(cleaned):
            raise serializers.ValidationError(
                "Enter a valid phone number, e.g. 03001234567 or +923001234567."
            )
        return cleaned

# NEW (F8/B22): what GET /api/v1/orders/checkout/prefill/ returns, and the
# body PUT /api/v1/orders/save-address/ accepts.
class CheckoutPrefillSerializer(serializers.Serializer):
    shipping_address = serializers.CharField(allow_blank=True, allow_null=True)
    city = serializers.CharField(allow_blank=True, allow_null=True)
    postal_code = serializers.CharField(allow_blank=True, allow_null=True)
    phone = serializers.CharField(allow_blank=True, allow_null=True)


class SaveAddressSerializer(serializers.Serializer):
    """PUT /api/v1/orders/save-address/ — B22: a standalone way to update the
    saved address, independent of going through checkout."""

    shipping_address = serializers.CharField(max_length=500)
    city = serializers.CharField(max_length=100)
    postal_code = serializers.CharField(
        required=False, allow_blank=True, max_length=20
    )
    phone = serializers.CharField(required=False, allow_blank=True, max_length=20)

    def validate_shipping_address(self, value):
        value = value.strip()
        if len(value) < 8:
            raise serializers.ValidationError(
                "Shipping address looks too short — please enter a full address."
            )
        return value

    def validate_postal_code(self, value):
        value = value.strip()
        if value and not POSTAL_CODE_RE.match(value):
            raise serializers.ValidationError(
                "Postal code should be 4-6 digits (leave blank if unknown)."
            )
        return value

    def validate_phone(self, value):
        cleaned = re.sub(r'[\s-]', '', value)
        if cleaned and not PHONE_RE.match(cleaned):
            raise serializers.ValidationError(
                "Enter a valid phone number, e.g. 03001234567 or +923001234567."
            )
        return cleaned

# Validates order status updates made by the admin.
class AdminOrderStatusSerializer(serializers.Serializer):
    # FIX (B30): "out_for_delivery" added — was missing, so admins had no
    # matching status to set once a shipment was actually on its way.
    status = serializers.ChoiceField(
        choices=[
            "pending_payment",
            "confirmed",
            "shipped",
            "out_for_delivery",
            "delivered",
            "cancelled",
        ]
    )

    tracking_number = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    # FIX (B27): admin cancelling an order must now give a reason — this
    # is only mandatory when status == "cancelled", enforced in validate()
    # below since a plain field-level required=True would also block every
    # non-cancel status update.
    cancellation_reason = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=1000,
    )

    def validate(self, attrs):
        if attrs.get("status") == "cancelled" and not attrs.get("cancellation_reason", "").strip():
            raise serializers.ValidationError(
                {"cancellation_reason": "Please provide a reason for cancelling this order."}
            )
        return attrs