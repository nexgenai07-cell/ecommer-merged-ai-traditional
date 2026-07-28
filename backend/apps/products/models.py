# PATH: apps/products/models.py
import uuid

from django.db import models
from django.conf import settings

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

    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    original_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    stock = models.PositiveIntegerField(default=0)

    # Auto-generated if left blank
    sku = models.CharField(
        max_length=100,
        unique=True,
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

    def __str__(self):
        return self.name


# Returns the primary image selected for this product.
    @property
    def primary_image(self):
        return self.images.filter(is_primary=True).first()

# Checks whether the product is currently available in stock.
    @property
    def in_stock(self):
        return self.stock > 0

# Automatically generates a unique SKU before saving a new product.
    def save(self, *args, **kwargs):
        # Generate SKU only if it is empty
        if not self.sku:
            while True:
                sku = f"SKU-{uuid.uuid4().hex[:8].upper()}"
                if not Product.objects.filter(
    sku=sku,
    is_delete=False,
).exists():
                    self.sku = sku
                    break

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
        ('order_placed', 'Order Placed (checkout)'),
        ('order_cancelled', 'Order Cancelled (stock restored)'),
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
    is_delete = models.BooleanField(default=False)
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