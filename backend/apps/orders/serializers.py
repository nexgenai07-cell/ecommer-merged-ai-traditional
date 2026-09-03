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
    # FIX (Cross-check, Sep 2026): spec locks this field's JSON name as
    # "method" (referenced throughout the PDF as "payment.method", e.g.
    # "the order's payment.method == 'qr'", "payment.method: 'qr'") — the
    # model field is still payment_method internally (source=), only the
    # serialized key changes, so no migration/internal-logic changes are
    # needed.
    method = serializers.CharField(source="payment_method")

    class Meta:
        model = Payment
        fields = [
            "id",
            "stripe_payment_intent_id",
            "status",
            "amount",
            "paid_at",
            # ============================================================
            # NEW: Payment method fields
            # ============================================================
            "method",
            "refund_method",
            "refund_transaction_reference",
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
            "cancellation_reason",
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


# ============================================================
# UPDATED: CheckoutSerializer with payment_method field
# as per PDF Part 3
# ============================================================
class CheckoutSerializer(serializers.Serializer):
    """POST /api/v1/orders/checkout/

    FIX (B15/B18/B19): shipping_address/city/postal_code/phone are kept
    here (required=False) for one-off manual entry at checkout time.
    shipping_address and city are the only two that are truly mandatory to
    place an order, and that's enforced in CheckoutView once the address
    resolution below has run, not here.

    NEW (Backend Change Request v2, Part 1): address_id (optional) — pick
    one of the customer's saved Address Book entries instead of typing the
    address in manually. CheckoutView resolves the final
    shipping_address/city/postal_code/phone in this order:
      1. address_id, if provided (must belong to this customer)
      2. shipping_address/city/... typed directly into this request
      3. the customer's Address Book entry with is_default=True
    If none of the three yield a shipping_address/city, checkout 400s —
    same "no address available" rule as before.

    REMOVED (Part 1): save_address. It used to write the single address
    straight onto Customer.address/city/postal_code — that was the old
    single-address behaviour the spec explicitly says to stop running in
    parallel with the Address Book. Saving an address is now only ever
    done explicitly via POST /api/v1/addresses/.

    ============================================================
    NEW (PDF Part 3): payment_method field
    ============================================================
    """

    address_id = serializers.IntegerField(required=False, allow_null=True)

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

    notes = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    # ============================================================
    # NEW: payment_method field (PDF Part 3)
    # ============================================================
    payment_method = serializers.ChoiceField(
        choices=["stripe", "qr"],
        required=True,
        help_text="Payment method: stripe or qr"
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


# NEW (Backend Change Request v2, Part 2 — Item 1 / Issue 3): optional
# reason on customer-initiated cancellation. Purely additive — sending no
# body at all (reason simply absent) must keep working exactly as before.
class CustomerOrderCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=1000,
    )


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

    # NEW (Backend Change Request v2, Part 2 — Item 2 / Issue 5): manual
    # refund proof for QR-paid orders. Both optional at the field level —
    # only actually required when this cancellation is for a QR-paid
    # order, which needs the order's payment.payment_method to check, so
    # that part of the validation happens in AdminOrderStatusUpdateView
    # (has the order loaded already) rather than here.
    refund_method = serializers.ChoiceField(
        choices=["manual", "automatic"],
        required=False,
    )
    refund_transaction_reference = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=255,
    )

    def validate(self, attrs):
        if attrs.get("status") == "cancelled" and not attrs.get("cancellation_reason", "").strip():
            raise serializers.ValidationError(
                {"cancellation_reason": "Please provide a reason for cancelling this order."}
            )
        return attrs