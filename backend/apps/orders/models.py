# PATH: apps/orders/models.py

from decimal import Decimal
from django.db import models
from django.conf import settings

# Stores customer information for each store.
# One user can have different customer profiles in different stores.
class Customer(models.Model):
    """Store-specific customer profile, auto-created on first order.

    FIX: user ab optional hai — guest checkout allow karne k liye.
    Anonymous customer bhi order place kar sakta hai (name + phone dekar),
    us waqt user=None save hoga. Jab wahi customer login k kar order karega,
    normal tarah user set hoga.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="customer_profiles",
        null=True,
        blank=True,
    )

    store = models.ForeignKey(
        "stores.Store",
        on_delete=models.CASCADE,
        related_name="customers",
    )

    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20)
    email = models.EmailField(null=True, blank=True)
    address = models.TextField(null=True, blank=True)

    # FIX (B18/B19/B22/F8): saved shipping details so checkout can prefill
    # and "Save Address" has somewhere to actually write to. postal_code is
    # intentionally optional (blank=True) — B18 asked for it to NOT be
    # mandatory.
    city = models.CharField(max_length=100, null=True, blank=True)
    postal_code = models.CharField(max_length=20, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

# Prevents duplicate customer profiles for the same user or phone number within a store.
    class Meta:
        db_table = "customers"
        unique_together = [
            ("user", "store"),
            ("phone", "store"),
        ]

# Returns customer's name and phone number.
    def __str__(self):
        return f"{self.name} ({self.phone})"


# NEW (Address Book — Backend Change Request v2, Part 1): replaces the old
# single-address-on-Customer system (customer.address/city/postal_code,
# served by the now-deprecated CheckoutPrefillView / SaveAddressView). A
# customer can now save multiple labelled addresses and pick one at
# checkout via address_id, instead of only ever having one address on file.
class Address(models.Model):
    """A saved shipping address belonging to a Customer profile.

    Exactly one Address per customer is ever is_default=True — enforced in
    save() below, not just at the API layer, so this invariant holds no
    matter which code path writes to the model.
    """

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="addresses",
    )

    label = models.CharField(max_length=100)
    shipping_address = models.TextField()
    city = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20, null=True, blank=True)
    phone = models.CharField(max_length=20, null=True, blank=True)
    is_default = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "addresses"
        ordering = ["-is_default", "-created_at"]

    def __str__(self):
        return f"{self.label} ({self.customer.name})"

    def save(self, *args, **kwargs):
        # "Exactly one address is default at all times" (Part 1, item 5) —
        # enforced here so it holds whether this address became the
        # default via the dedicated set-default endpoint, or by being
        # created/edited with is_default=True.
        if self.is_default:
            Address.objects.filter(
                customer_id=self.customer_id,
                is_default=True,
            ).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


# Stores the main order information after checkout.
class Order(models.Model):
    # Defines all possible order statuses.
    # FIX (B30): "out_for_delivery" added — was completely missing before,
    # so admins had no way to mark an order as on its way.
    STATUS_CHOICES = [
        ("pending_payment", "Pending Payment"),
        ("confirmed", "Confirmed"),
        ("shipped", "Shipped"),
        ("out_for_delivery", "Out for Delivery"),
        ("delivered", "Delivered"),
        ("cancelled", "Cancelled"),
    ]

    store = models.ForeignKey(
        "stores.Store",
        on_delete=models.CASCADE,
        related_name="orders",
    )

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="orders",
    )

    order_number = models.CharField(max_length=20, unique=True)

    # NEW (Shipping cost fix — Sep 2026): shipping was never added to
    # total_amount anywhere (checkout, Stripe intent, or QR amount).
    # These two fields snapshot the shipping choice made at checkout time
    # and must never be recalculated/overwritten after payment confirmation
    # — only status/payment.status change post-checkout.
    SHIPPING_METHOD_CHOICES = [
        ("standard", "Standard"),
        ("express", "Express"),
    ]

    SHIPPING_COSTS = {
        "standard": Decimal("299.00"),
        "express": Decimal("999.00"),
    }

    shipping_method = models.CharField(
        max_length=10,
        choices=SHIPPING_METHOD_CHOICES,
        default="standard",
    )

    shipping_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("299.00"),
    )

    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending_payment",
    )

    shipping_address = models.TextField()

    # FIX (B18/B19): city + postal_code are now real, separately validated
    # fields on the order (snapshot at checkout time) instead of being
    # smushed into the single shipping_address text blob. postal_code stays
    # optional per B18.
    city = models.CharField(max_length=100, blank=True, default="")
    postal_code = models.CharField(max_length=20, blank=True, default="")

    # FIX (B15): contact number captured explicitly at checkout time and
    # validated (see CheckoutSerializer.validate_contact_phone), instead of
    # silently falling back to whatever happens to be on the user's account.
    contact_phone = models.CharField(max_length=20, blank=True, default="")

    tracking_number = models.CharField(
        max_length=100,
        null=True,
        blank=True,
    )

    notes = models.TextField(
        null=True,
        blank=True,
    )

    # FIX (B27): admin ab cancel karte waqt reason dena mandatory hai —
    # ye field wahi reason store karti hai (order fairly cancel hua, koi
    # bhi baad mein wajah dekh sakta hai).
    cancellation_reason = models.TextField(
        null=True,
        blank=True,
    )

    # FIX (B59): stock ab checkout pe nahi, is flag ke true hone pe (yani
    # payment confirm hone ke baad) deduct hoti hai. Ye guard rakhta hai
    # taake ek order ki stock kabhi do baar deduct/restore na ho.
    stock_deducted = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

# Orders are displayed with newest orders first.
    class Meta:
        db_table = "orders"
        ordering = ["-created_at"]

# Returns the order number for easy identification.
    def __str__(self):
        return self.order_number


# Stores every product purchased in an order.
# Each row represents one product inside an order.
class OrderItem(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.SET_NULL,
        null=True,
    )

    product_name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField()
    total_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        db_table = "order_items"

# Returns quantity and product name.
    def __str__(self):
        return f"{self.quantity} x {self.product_name}"


# Stores payment information for each order.
# One payment record exists for one order.
class Payment(models.Model):
    # UPDATED: New status choices as per PDF Part 3
    # Exactly these five values: pending | under_review | paid | rejected | refunded
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("under_review", "Under Review"),      # NEW - QR proof uploaded, waiting admin
        ("paid", "Paid"),
        ("rejected", "Rejected"),              # NEW - QR proof rejected
        ("refunded", "Refunded"),
    ]

    # NEW: Payment method choices as per PDF Part 3
    METHOD_CHOICES = [
        ("stripe", "Stripe"),
        ("qr", "QR"),
    ]

    # NEW: Refund method choices as per PDF Part 2 Item 2
    REFUND_METHOD_CHOICES = [
        ("manual", "Manual"),
        ("automatic", "Automatic"),
    ]

    order = models.OneToOneField(
        Order,
        on_delete=models.CASCADE,
        related_name="payment",
    )

    stripe_payment_intent_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )

    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    paid_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    # FIX (B29): "no refund proof" — jab bhi refund hota hai (order cancel
    # hone par), timestamp yahan record hota hai taake customer/admin ko
    # confirmation mile ke refund process ho chuka hai.
    refunded_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    # ============================================================
    # NEW FIELDS as per Backend Change Request v2 (PDF)
    # ============================================================

    # Part 3: QR Payment Method
    payment_method = models.CharField(
        max_length=10,
        choices=METHOD_CHOICES,
        default="stripe",
        help_text="Payment method used: stripe or qr"
    )

    # Part 2 Item 2: Manual refund proof for QR-paid orders
    refund_method = models.CharField(
        max_length=10,
        choices=REFUND_METHOD_CHOICES,
        null=True,
        blank=True,
        help_text="manual for QR orders, automatic for Stripe orders"
    )

    refund_transaction_reference = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Required when refund_method is manual"
    )

    # Part 3.1: QR Proof Upload fields
    qr_screenshot_url = models.TextField(
        null=True,
        blank=True,
        help_text="URL of uploaded QR payment screenshot"
    )

    qr_transaction_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Transaction ID provided by customer"
    )

    qr_submitted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When proof was uploaded (moves to under_review)"
    )

    qr_reject_reason = models.TextField(
        null=True,
        blank=True,
        help_text="Reason given when admin rejects QR proof"
    )

    qr_image_hash = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text="SHA-256 hash of screenshot for duplicate detection"
    )

    # Part 3.6: Duplicate warning flag
    qr_duplicate_warning = models.BooleanField(
        default=False,
        help_text="Set to True if duplicate proof detected"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payments"

    def __str__(self):
        return f"Payment for {self.order.order_number}"