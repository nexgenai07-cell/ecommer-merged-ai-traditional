# PATH: apps/products/models.py
import uuid

from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError

# Main product model that stores all product information.
class Product(models.Model):
    store = models.ForeignKey(
        "stores.Store",
        on_delete=models.CASCADE,
        related_name="products",
    )
    category = models.ForeignKey(
        "categories.Category",
        on_delete=models.SET_NULL,
        related_name="products",
        null=True,
        blank=True,
    )

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    original_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )

    # ============================================================
    # CHANGED: Single 'stock' field replaced with three fields
    # as per PDF Part 2 Item 5 (Reserved Stock)
    # ============================================================
    total_stock = models.PositiveIntegerField(
        default=0,
        help_text="Total physical stock available"
    )
    reserved_stock = models.PositiveIntegerField(
        default=0,
        help_text="Stock reserved for pending payment orders"
    )

    # NOTE: available_stock is a computed property (total_stock - reserved_stock)
    # NOT a database field

    # Keep old field for backward compatibility during migration
    # Will be removed after data migration
    stock = models.PositiveIntegerField(default=0, help_text="DEPRECATED: Use total_stock instead")

    # Auto-generated if left blank
    # NOTE (Sep 2026): this auto-generation is now unreachable through the
    # normal API — ProductCreateUpdateSerializer.sku is required=True — but
    # is kept here as a DB-level safety net for any other code path
    # (management commands, data migrations, admin-site direct saves) that
    # creates a Product without going through that serializer.
    sku = models.CharField(
        max_length=25,
        blank=True,
    )

    is_active = models.BooleanField(default=True)
    is_delete = models.BooleanField(default=False)
    publish_at = models.DateTimeField(null=True, blank=True)
    low_stock_threshold = models.PositiveIntegerField(default=5)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "products"
        ordering = ["-created_at"]
        # FIX (Soft-delete name reuse bug report, Sep 2026): "name" used
        # to be globally unique=True, which blocked creating a new
        # product with the same name as a *soft-deleted* one forever —
        # the deleted row still occupies the name in the DB even though
        # it's invisible everywhere else. Replaced with a partial unique
        # constraint that only applies among non-deleted rows, so a
        # deleted product's name genuinely frees up for reuse.
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(is_delete=False),
                name="unique_active_product_name",
            ),
            # FIX (Soft-delete SKU reuse, Sep 2026): same issue as name
            # above — sku was globally unique=True, so a soft-deleted
            # product's SKU stayed permanently blocked. Scoped to
            # is_delete=False only, matching the validate_sku check.
            models.UniqueConstraint(
                fields=["sku"],
                condition=models.Q(is_delete=False),
                name="unique_active_product_sku",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def primary_image(self):
        return self.images.filter(is_primary=True).first()

    # ============================================================
    # UPDATED: in_stock now uses available_stock as per PDF Part 2 Item 5
    # ============================================================
    @property
    def in_stock(self):
        return self.available_stock > 0

    # ============================================================
    # NEW: available_stock computed property as per PDF Part 2 Item 5
    # available_stock = total_stock - reserved_stock
    # ============================================================
    @property
    def available_stock(self):
        return self.total_stock - self.reserved_stock

    # ============================================================
    # NEW: Validate that reserved_stock never exceeds total_stock
    # ============================================================
    def clean(self):
        if self.reserved_stock > self.total_stock:
            raise ValidationError({
                'reserved_stock': 'Reserved stock cannot exceed total stock.'
            })

    def save(self, *args, **kwargs):
        # Auto-generate SKU if empty
        if not self.sku:
            while True:
                sku = f"SKU-{uuid.uuid4().hex[:8].upper()}"
                if not Product.objects.filter(sku=sku).exists():
                    self.sku = sku
                    break

        # Validate before saving
        self.clean()
        super().save(*args, **kwargs)


from cloudinary.models import CloudinaryField


# Stores multiple images for each product.
class ProductImage(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image = CloudinaryField("image", blank=True, null=True)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "product_images"

    def __str__(self):
        return f"Image for {self.product.name}"


# Keeps a history of product price and stock changes.
class ProductHistory(models.Model):
    product    = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='history')
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    old_price  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    new_price  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    old_stock  = models.IntegerField(null=True, blank=True)
    new_stock  = models.IntegerField(null=True, blank=True)
    reason     = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'product_history'
        ordering = ['-created_at']


# NEW (stock race-condition fix — full audit trail): dedicated table for
# every stock movement, whether triggered manually by an admin via the
# new POST /stock/adjust/ endpoint, or automatically by checkout, order
# cancellation, or return approval. 'reason' choices include the 5
# manual-adjustment reasons from the endpoint contract, plus internal
# system reasons used only by checkout/cancel/return flows (never
# accepted directly from the adjust endpoint's request body).

# Records every stock increase or decrease for auditing purposes.
class StockMovement(models.Model):
    REASON_CHOICES = [
        ('restock', 'Restock'),
        ('damaged', 'Damaged'),
        ('correction', 'Correction'),
        ('return', 'Return'),
        ('other', 'Other'),
        # Internal/system-triggered reasons (not exposed via the manual
        # adjust endpoint's serializer choices):
        ('order_placed', 'Order Placed (reserved)'),
        ('order_cancelled', 'Order Cancelled (released)'),
        ('order_confirmed', 'Order Payment Confirmed (deducted)'),
        # NEW: QR specific reasons
        ('qr_approved', 'QR Payment Approved'),
        ('qr_rejected', 'QR Payment Rejected'),
        ('qr_timeout', 'QR Payment Timeout'),
        ('stripe_timeout', 'Stripe Payment Timeout'),
    ]

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='stock_movements',
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Null for system-triggered movements (checkout, auto-cancel, etc).",
    )
    old_stock = models.IntegerField()
    new_stock = models.IntegerField()
    delta = models.IntegerField()
    reason = models.CharField(max_length=30, choices=REASON_CHOICES)
    note = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'stock_movements'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.product.name}: {self.old_stock} -> {self.new_stock} ({self.reason})"


# Stores discount coupons created by the admin.
class Discount(models.Model):
    TYPE_CHOICES = [
        ('percent', 'Percentage'),
        ('fixed',   'Fixed Amount'),
    ]

    store            = models.ForeignKey('stores.Store', on_delete=models.CASCADE, related_name='discounts')
    code             = models.CharField(max_length=50, unique=True)
    type             = models.CharField(max_length=10, choices=TYPE_CHOICES)
    value            = models.DecimalField(max_digits=10, decimal_places=2)
    min_order_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    start_date       = models.DateTimeField()
    end_date         = models.DateTimeField()
    is_active        = models.BooleanField(default=True)
    is_delete        = models.BooleanField(default=False)
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'discounts'

    def __str__(self):
        return self.code


# Links products with discount coupons (many-to-many relationship).
class ProductDiscount(models.Model):
    discount   = models.ForeignKey(Discount, on_delete=models.CASCADE, related_name='product_discounts')
    product    = models.ForeignKey(Product,  on_delete=models.CASCADE, related_name='product_discounts')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'product_discounts'
        unique_together = ['discount', 'product']


# Stores daily sales statistics for each product.
class ProductStats(models.Model):
    product       = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stats')
    store         = models.ForeignKey('stores.Store', on_delete=models.CASCADE)
    date          = models.DateField()
    total_sold    = models.IntegerField(default=0)
    total_revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'product_stats'
        unique_together = ['product', 'date']
        ordering        = ['-date']


# NEW: Product reviews + star ratings for the product detail page
# (rating average, "Based on N reviews" breakdown bars, individual
# review cards with a "Verified Buyer" badge, and "Write a Review").
#
# One review per user per product — enforced at the DB level via the
# partial unique constraint below (only among is_delete=False rows, same
# soft-delete pattern already used for Product.name/sku), so re-reviewing
# means editing the existing review (PUT), not creating a second one.
class Review(models.Model):
    RATING_CHOICES = [(i, str(i)) for i in range(1, 6)]

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="reviews",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="product_reviews",
    )
    rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES)
    comment = models.TextField(blank=True, default="")

    # Set automatically by the create endpoint (review_views.py) based on
    # whether this user has a delivered order containing this product —
    # never accepted directly from the request body.
    is_verified_purchase = models.BooleanField(default=False)

    # Lets an admin hide an inappropriate review without deleting it
    # outright (separate from is_delete, which is the customer's own
    # "delete my review" action).
    is_active = models.BooleanField(default=True)
    is_delete = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_reviews"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "user"],
                condition=models.Q(is_delete=False),
                name="unique_active_review_per_user_per_product",
            ),
        ]

    def __str__(self):
        return f"{self.user.email} rated {self.product.name} {self.rating}/5"