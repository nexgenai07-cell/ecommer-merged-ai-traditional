# PATH: apps/orders/models.py

from decimal import Decimal
from datetime import timedelta
from django.db import models
from django.conf import settings
from django.utils import timezone

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
        # FIX (Sep 2026 — IntegrityError on /api/v1/addresses/ and
        # anywhere else get_or_create_customer() runs, for any user with
        # no phone number on their account):
        #
        # unique_together = [("phone", "store")] used to apply to EVERY
        # row, including ones where phone is blank ("") — e.g. a user who
        # signed up via Google and never set a phone number gets
        # Customer.phone="" on first-ever create. Postgres treats "" as
        # an ordinary, comparable value (unlike NULL, which is never
        # equal to another NULL), so the SECOND user in the same store
        # who also has no phone crashes with a duplicate-key
        # IntegrityError the moment their Customer row is first created —
        # completely unrelated to whatever they were actually trying to
        # do (in the reported case, saving an address).
        #
        # Fix: keep (user, store) as a plain unique constraint — that one
        # was always fine, since Postgres already treats multiple NULL
        # users (guest customers) in the same store as distinct. For
        # (phone, store), only enforce uniqueness when phone is actually
        # set — condition=~Q(phone="") — so any number of customers with
        # no phone on file can coexist in the same store, exactly like
        # multiple guest customers already could.
        constraints = [
            models.UniqueConstraint(
                fields=["user", "store"],
                name="customers_user_store_uniq",
            ),
            models.UniqueConstraint(
                fields=["phone", "store"],
                condition=~models.Q(phone=""),
                name="customers_phone_store_nonblank_uniq",
            ),
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
        # NEW (Sep 2026 — QR 10-minute upload window): the very first
        # state of a QR-payment order, from the moment checkout succeeds
        # until the customer either uploads their payment proof or the
        # window expires. Payment.status stays "" (empty) the whole time
        # this is the order's status — see Payment.status below and
        # apps/payments/views.py:QRProofUploadView. Stripe orders skip
        # this entirely and still start at "pending_payment" as before.
        ("order_placed", "Order Placed"),
        ("pending_payment", "Pending Payment"),
        # NEW (Supervisor scenario 4, Sep 2026): distinct from
        # pending_payment — set when a customer re-uploads QR proof
        # after a prior rejection, so admin's queue can tell a fresh
        # first-time review apart from a retry review.
        ("on_hold", "On Hold"),
        ("confirmed", "Confirmed"),
        ("shipped", "Shipped"),
        ("out_for_delivery", "Out for Delivery"),
        ("delivered", "Delivered"),
        ("cancelled", "Cancelled"),
    ]

    # NEW (Sep 2026 — Total Spent / Revenue consistency fix): single
    # source of truth for "this order's money counts as spent/revenue".
    # A customer has genuinely paid once the order reaches confirmed, and
    # that stays true through shipped / out_for_delivery / delivered —
    # out_for_delivery is included because payment already happened
    # before that stage. pending_payment and on_hold are excluded because
    # payment isn't confirmed yet at those stages. cancelled is excluded
    # because a cancelled order's payment gets refunded (see
    # AdminOrderStatusUpdateView in views.py — a refund always sets
    # order.status = "cancelled", there is no separate "refunded" order
    # status), so a refunded order is already covered by excluding
    # "cancelled" here.
    #
    # Used by customer_serializers.py (admin customer list), customer_views.py
    # (same list's sort annotation), customer_stats_views.py (customer's own
    # dashboard), and apps/analytics/dashboard_views.py (admin revenue
    # dashboard + CSV export) — so all four "money" numbers always agree.
    REVENUE_STATUSES = ["confirmed", "shipped", "out_for_delivery", "delivered"]

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
        # NEW (Sep 2026 — QR 10-minute upload window): a QR order's
        # payment row is created with status="" (empty) while the order
        # itself is "order_placed" — nothing to review yet since no proof
        # was uploaded. It only becomes "pending" once... actually it
        # goes straight from "" to "under_review" the moment proof is
        # uploaded (see QRProofUploadView), same single step as before.
        # Stripe payments are untouched and still default to "pending".
        blank=True,
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

    # NEW (Supervisor scenario 3 — QR reject workflow, Sep 2026): counts
    # how many times this order's QR proof has been rejected by an admin.
    # Incremented once per rejection, on AdminQRPaymentRejectView. Lets
    # the admin dashboard show "Rejected 2x" etc., and can later be used
    # to cap how many re-upload attempts a customer gets.
    qr_rejection_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of times this order's QR proof has been rejected",
    )

    # NEW (Sep 2026 — QR 10-minute upload window): set at checkout time
    # (created_at + 10 minutes) for QR orders only. QRProofUploadView
    # refuses an upload past this deadline, and a new scheduled job
    # (cancel_stale_qr_placements, see the management command) auto-
    # cancels any order still "order_placed" once this passes.
    qr_upload_deadline = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Deadline to upload QR proof before auto-cancel (order_placed stage only)",
    )

    # NEW (Sep 2026 — QR 10-minute upload window): the customer gets ONE
    # "need more time?" extension (+5 minutes) after the initial 10-minute
    # window passes — see ExtendQRUploadTimeView. Sticks at True even
    # after that extended deadline also passes, so a second extension
    # request is refused.
    qr_extension_used = models.BooleanField(
        default=False,
        help_text="Whether the one-time +5 minute upload extension has already been used",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payments"

    def __str__(self):
        return f"Payment for {self.order.order_number}"


# UPDATED (Sep 2026 — Checkout OTP verification made permanent): a
# customer must verify a 6-digit code emailed to them ONCE, the very
# first time they check out. One row per user — sending a fresh code
# overwrites the previous one while verification is still pending.
#
# FIX (Bug report, Sep 2026): verification used to be single-use and
# time-boxed (see the now-unused `consumed_at` field and
# VERIFICATION_WINDOW_MINUTES below) — every single order forced a
# fresh send-otp/verify-otp round trip, even for a customer who had
# already verified minutes or days earlier. That was never the intent;
# once `is_verified` is True it now stays usable forever, so a customer
# only ever verifies once for their account and every order afterwards
# (this one and all future ones) reuses that same verified row.
# `consumed_at` / VERIFICATION_WINDOW_MINUTES are kept on the model
# (no migration needed) but are no longer read by is_verification_usable().
#
# Phone/SMS delivery is a planned follow-up (out of scope for now) —
# this only guards email delivery.
class CheckoutOTP(models.Model):
    """
    Gate on CheckoutView: an order can only be created once this user has
    a usable (verified) row here. Verification, once completed, never
    expires and is never "spent" — it authorizes every order this
    customer places from then on.
    """

    # How long a code is valid to be entered (send -> verify).
    OTP_VALIDITY_MINUTES = 10

    # How long a *verified* code stays usable to actually place the order
    # (verify -> checkout). Kept separate from OTP_VALIDITY_MINUTES so a
    # customer who verifies and then keeps shopping for a few minutes
    # doesn't get blocked at the last step.
    VERIFICATION_WINDOW_MINUTES = 30

    # Minimum gap between two "send OTP" requests for the same user, to
    # stop the email endpoint being hammered.
    RESEND_COOLDOWN_SECONDS = 60

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="checkout_otp",
    )

    # Snapshot of the address the code was actually sent to — kept
    # separate from user.email so a later email change can't silently
    # invalidate/relocate an in-flight verification.
    email = models.EmailField()

    otp_code = models.CharField(max_length=6, blank=True, null=True)
    otp_expires_at = models.DateTimeField(blank=True, null=True)

    is_verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(blank=True, null=True)

    # Set once this verified code has actually been used to place an
    # order — a customer must send + verify a new code for the next one.
    consumed_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "checkout_otps"

    def __str__(self):
        state = "verified" if self.is_verified else "pending"
        return f"Checkout OTP for {self.email} ({state})"

    def is_code_valid(self, code):
        """Code matches and hasn't expired yet."""
        if not self.otp_code or not self.otp_expires_at:
            return False
        if self.otp_code != code:
            return False
        return timezone.now() < self.otp_expires_at

    def is_verification_usable(self):
        """
        True if this customer has ever completed checkout verification.

        FIX (Bug report, Sep 2026): this used to also require
        `not self.consumed_at` (unused-on-a-previous-order) and being
        inside a post-verification time window — meaning a customer had
        to re-verify before every single order. Verification is now
        permanent: once `is_verified` is set, it authorizes this order
        and every future order for this account, with no expiry and no
        "one order per code" limit.
        """
        return bool(self.is_verified)