# PATH: apps/orders/serializers.py

import re

from rest_framework import serializers
from .models import Customer, Order, OrderItem, Payment
from .locations import check_city_province

# Pakistani mobile/landline numbers: optional +92 or leading 0, then 9-11
# digits. Kept permissive on purpose (spaces/dashes stripped before check)
# so real numbers aren't rejected, but garbage input is (B15).
PHONE_RE = re.compile(r'^(\+92|0)\d{9,10}$')

# Pakistan Post uses 5-digit postal codes. Field stays optional (B18) —
# this only runs when the customer actually typed something in.
POSTAL_CODE_RE = re.compile(r'^\d{4,6}$')

# City must be letters/spaces only (e.g. "Rahim Yar Khan") — no digits,
# no symbols. Max length enforced separately via CharField(max_length=...).
CITY_RE = re.compile(r'^[A-Za-z\s]+$')
CITY_MAX_LENGTH = 30

# NEW (Bug fix, Sep 2026): single source of truth for whether the customer
# should still be offered "Cancel Order" / "Track Order" on an order.
#   - Cancel: only while the order hasn't shipped yet, and obviously not
#     once it's already cancelled (by the customer, by an admin, or
#     automatically after 3 rejected QR proofs).
#   - Track: hidden once the order is cancelled — there is nothing left
#     to track.
# Used by OrderListSerializer / OrderDetailSerializer below (so the
# frontend just reads can_cancel / can_track) and by OrderTrackView in
# views.py (so the API itself also refuses, even if a button is shown).
CUSTOMER_CANCELLABLE_STATUSES = ("pending_payment", "on_hold", "confirmed")


def order_can_cancel(order):
    return order.status in CUSTOMER_CANCELLABLE_STATUSES


def order_can_track(order):
    return order.status != "cancelled"


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

    # FIX (Cross-check, Sep 2026 — frontend report on ORD-2026-00063):
    # screenshot_url was already saved correctly on Payment.qr_screenshot_url
    # at proof-upload time (confirmed working — it's already returned by
    # GET /api/v1/admin/payments/qr/pending/), it just was never exposed on
    # this serializer, so it never reached the customer or admin Order
    # Detail responses. SerializerMethodField (not a plain CharField) so a
    # None value comes through as JSON null instead of the literal string
    # "None".
    screenshot_url = serializers.SerializerMethodField()

    # FIX (Sep 2026 — "Card via Stripe" wrongly shown on QR orders): the
    # frontend order-tracking page was hardcoding "Card via Stripe" as the
    # payment label because this serializer never gave it anything to
    # branch on for QR orders — qr_transaction_id (the QR reference the
    # customer entered at proof-upload) was captured on the model but
    # never exposed here. Two additions fix this at the source instead of
    # relying on the frontend guessing a label:
    #   - qr_transaction_id: the raw reference number, only meaningful
    #     when method == "qr" (null for stripe orders).
    #   - reference / method_label: a ready-to-render pair so the
    #     frontend doesn't need its own if/else on method — for stripe,
    #     reference is the Stripe payment_intent id; for qr, it's the
    #     qr_transaction_id.
    reference = serializers.SerializerMethodField()
    method_label = serializers.SerializerMethodField()

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
            "method_label",
            "reference",
            "qr_transaction_id",
            "refund_method",
            "refund_transaction_reference",
            "screenshot_url",
            # FIX (12-scenario QA completeness check, Sep 2026): tracked
            # correctly on the model since Scenario 3/7, but was never
            # actually exposed anywhere an admin or customer could see it
            # (order detail, admin pending queue) — only showed up in the
            # reject/re-upload endpoints' own immediate response. Exposed
            # here now under a clearer name so it reads on every order
            # detail view.
            "qr_rejection_count",
        ]

    def get_screenshot_url(self, obj):
        return obj.qr_screenshot_url or None

    def get_method_label(self, obj):
        if obj.payment_method == "qr":
            return "QR Payment"
        return "Card via Stripe"

    def get_reference(self, obj):
        if obj.payment_method == "qr":
            return obj.qr_transaction_id or None
        return obj.stripe_payment_intent_id or None
    

# Returns a lightweight order summary for customer order history.
class OrderListItemSerializer(serializers.ModelSerializer):
    """
    Lightweight item preview used by the customer order-history list.
    Returns only the product name and primary product image.
    """

    product_image = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            "product_name",
            "product_image",
        ]

    def get_product_image(self, obj):
        product = obj.product

        # Product may have been deleted after the order was placed.
        # product_name is still preserved on OrderItem.
        if not product:
            return None

        img = (
            product.images.filter(is_primary=True).first()
            or product.images.first()
        )

        if not img or not img.image:
            return None

        image = img.image

        if hasattr(image, "url"):
            return image.url.replace("http://", "https://")

        return str(image)


class OrderListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for customer order history (My Orders).

    items contains only the first 3 OrderItems for thumbnail preview.
    item_count contains the real total number of items in the order.
    """

    items = serializers.SerializerMethodField()
    item_count = serializers.SerializerMethodField()

    # NEW (Bug fix, Sep 2026): tells the frontend whether to show the
    # "Cancel Order" / "Track Order" buttons for this order.
    can_cancel = serializers.SerializerMethodField()
    can_track = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "total_amount",
            "discount_amount",
            "shipping_method",
            "shipping_cost",
            "status",
            "item_count",
            "created_at",
            "items",
            "can_cancel",
            "can_track",
        ]

    def get_can_cancel(self, obj):
        return order_can_cancel(obj)

    def get_can_track(self, obj):
        return order_can_track(obj)

    def get_items(self, obj):
        # Only the first 3 items are needed for the My Orders preview.
        items = list(obj.items.all()[:3])

        return OrderListItemSerializer(
            items,
            many=True,
            context=self.context,
        ).data

    def get_item_count(self, obj):
        # IMPORTANT: this is the real total, NOT len(items).
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
            "shipping_method",
            "shipping_cost",
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

    # NEW (Bug fix, Sep 2026): same flags as OrderListSerializer, so the
    # order detail / tracking page also knows whether to show the
    # "Cancel Order" / "Track Order" buttons.
    can_cancel = serializers.SerializerMethodField()
    can_track = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "status",
            "total_amount",
            "discount_amount",
            "shipping_method",
            "shipping_cost",
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
            "can_cancel",
            "can_track",
        ]

    def get_can_cancel(self, obj):
        return order_can_cancel(obj)

    def get_can_track(self, obj):
        return order_can_track(obj)

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

    NEW (Sep 2026 — city/province check): province (the dropdown value)
    is now sent together with a manually typed city, and the two must
    match — e.g. city "Gujranwala" with province "Sindh" is rejected
    (Gujranwala is in Punjab). See validate() at the bottom of this class
    and apps/orders/locations.py for the city list. This only runs when
    the address is typed in manually: if the customer picked a saved
    address (address_id) or the city is left blank (falls back to their
    default saved address), there is no typed city/province pair to
    compare. A city that isn't in our list is not rejected.

    NEW (Buy Now): buy_now_product_id / buy_now_quantity — when
    buy_now_product_id is present, CheckoutView builds the order from
    this single product+quantity instead of the persisted cart, and the
    cart is left completely untouched. Both are optional; a normal cart
    checkout (the existing behaviour) keeps working exactly as before
    when they're omitted.
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
        max_length=CITY_MAX_LENGTH,
    )
    # NEW (Sep 2026): the province dropdown value. Required whenever a
    # city is typed in manually (enforced in validate() below) so the
    # city/province pair can be checked. It is only used for that check —
    # it is not stored on the order.
    province = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=50,
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

    # NEW (Shipping cost fix — Sep 2026): required. ChoiceField already
    # 400s on a missing field or any value other than "standard"/"express",
    # per spec.
    shipping_method = serializers.ChoiceField(
        choices=["standard", "express"],
        required=True,
        help_text="Shipping method: standard (Rs. 299) or express (Rs. 999)",
    )

    # NEW (Buy Now): optional. Present only when the customer clicked
    # "Buy Now" on a product detail page instead of checking out their
    # cart.
    buy_now_product_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="Buy Now: id of the single product to order, bypassing the cart.",
    )
    buy_now_quantity = serializers.IntegerField(
        required=False,
        min_value=1,
        default=1,
        help_text="Buy Now: quantity of buy_now_product_id to order. Defaults to 1.",
    )

    # NEW (Checkout coupon field): optional. Lets the customer apply a
    # coupon directly from the checkout page, instead of only from the
    # cart page's separate "apply coupon" action.
    # UPDATED (Supervisor request, Sep 2026): now also applies to Buy
    # Now checkouts (validated against the Buy Now product's own
    # price × quantity) — it is only NOT written to the persisted cart
    # in that case, since Buy Now never touches the cart.
    coupon_code = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=50,
        help_text="Optional coupon code to apply at checkout (cart or Buy Now).",
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
        if value and not CITY_RE.match(value):
            raise serializers.ValidationError(
                "City name should contain letters only (no numbers or symbols)."
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

    # NEW (Sep 2026): the typed city must belong to the selected province.
    def validate(self, attrs):
        # A saved Address Book entry was chosen — CheckoutView uses that
        # entry's city and ignores any typed city, so there's nothing to
        # compare here.
        if attrs.get("address_id"):
            return attrs

        city = (attrs.get("city") or "").strip()

        # No city typed: CheckoutView will fall back to the customer's
        # default saved address (or 400 with its own "missing city"
        # message). Nothing typed to compare, so nothing to check.
        if not city:
            return attrs

        province = (attrs.get("province") or "").strip()
        if not province:
            raise serializers.ValidationError(
                {"province": "Please select your province."}
            )

        canonical_province, error = check_city_province(city, province)
        if error:
            raise serializers.ValidationError({"province": error})

        attrs["province"] = canonical_province
        return attrs


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

# Serializes saved checkout details returned by the prefill endpoint.
class CheckoutPrefillSerializer(serializers.Serializer):
    shipping_address = serializers.CharField(
        allow_blank=True,
        allow_null=True,
    )
    city = serializers.CharField(
        allow_blank=True,
        allow_null=True,
    )
    postal_code = serializers.CharField(
        allow_blank=True,
        allow_null=True,
    )
    phone = serializers.CharField(
        allow_blank=True,
        allow_null=True,
    )


# Validates the customer's saved-address update request.
# Validates the customer's saved-address update request.
class SaveAddressSerializer(serializers.Serializer):
    shipping_address = serializers.CharField(max_length=500)
    city = serializers.CharField(max_length=CITY_MAX_LENGTH)
    postal_code = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=20,
    )
    phone = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=20,
    )

    # FIX: this serializer had no field validation at all — city and
    # postal_code were saved straight to the Customer record as typed,
    # so letters in postal_code or digits/symbols in city went through.
    def validate_city(self, value):
        value = value.strip()
        if value and not CITY_RE.match(value):
            raise serializers.ValidationError(
                "City name should contain letters only (no numbers or symbols)."
            )
        return value

    def validate_postal_code(self, value):
        value = (value or "").strip()
        if value and not POSTAL_CODE_RE.match(value):
            raise serializers.ValidationError(
                "Postal code should be 4-6 digits (leave blank if unknown)."
            )
        return value

    def validate_phone(self, value):
        cleaned = re.sub(r'[\s-]', '', value or "")
        if cleaned and not PHONE_RE.match(cleaned):
            raise serializers.ValidationError(
                "Enter a valid phone number, e.g. 03001234567 or +923001234567."
            )
        return cleaned