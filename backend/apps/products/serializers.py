# PATH: apps/products/serializers.py

import re

from rest_framework import serializers
from django.db.models import Avg, Sum
from .models import Product, ProductImage, ProductHistory, StockMovement


# NEW (Production SKU validation spec, Sep 2026)
# Enforces: starts AND ends with a letter/number, middle characters may be
# A-Z, 0-9, hyphen, or underscore. This does NOT by itself enforce the
# minimum length of 3 (a bare single character like "A" also matches) or
# reject consecutive special characters like "--"/"__" — both of those are
# checked separately in validate_sku() below, since the regex alone can't
# express them cleanly.
SKU_REGEX = re.compile(r"^[A-Z0-9](?:[A-Z0-9_-]{1,23}[A-Z0-9])?$")

SKU_MIN_LENGTH = 3
SKU_MAX_LENGTH = 25

# NEW (Production SKU validation spec, Sep 2026): these exact strings can
# never be used as a SKU, checked after uppercasing — so "null", "Null",
# and "NULL" are all blocked alike.
RESERVED_SKUS = {"NULL", "TEST", "ADMIN"}


# Returns basic category information inside product responses.
class CategorySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


# Converts product image data into API response format.
class ProductImageSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = [
            "id",
            "image_url",
            "is_primary",
            "created_at",
        ]

    def get_image_url(self, obj):
        if obj.image:
            return obj.image.url.replace("http://", "https://")
        return None


# ============================================================
# UPDATED: ProductListSerializer with new stock fields
# as per PDF Part 2 Item 5
# ============================================================
class ProductListSerializer(serializers.ModelSerializer):
    primary_image = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()

    # ============================================================
    # NEW: available_stock computed field
    # ============================================================
    available_stock = serializers.SerializerMethodField()

    # NEW (Sep 2026 — profit tracking): both admin-only. Resolve to None
    # for a customer request so cost/profit never leaks to the storefront
    # — this serializer is shared between the public product list and the
    # admin panel's product list.
    purchase_price = serializers.SerializerMethodField()
    profit = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "price",
            "original_price",
            # NEW (Sep 2026 — profit tracking): admin-only, null for customers
            "purchase_price",
            "profit",
            # ============================================================
            # NEW: Replace single 'stock' with three fields
            # ============================================================
            "total_stock",
            "reserved_stock",
            "available_stock",  # computed: total_stock - reserved_stock
            "in_stock",         # available_stock > 0
            "sku",
            "category",
            "primary_image",
            "is_active",
        ]

    # Returns category details instead of only the category ID.
    def get_category(self, obj):
        if not obj.category:
            return None

        return {
            "id": obj.category.id,
            "name": obj.category.name,
        }

    # Returns the primary product image URL.
    def get_primary_image(self, obj):
        images = list(obj.images.all())

        primary = next(
            (img for img in images if img.is_primary),
            None,
        )

        img = primary or (images[0] if images else None)

        if not img or not img.image:
            return None

        return img.image.url.replace("http://", "https://")

    # NEW (Sep 2026 — profit tracking): True only for an authenticated
    # admin making the request.
    def _is_admin(self):
        request = self.context.get("request")
        return bool(
            request
            and request.user
            and request.user.is_authenticated
            and getattr(request.user, "role", None) == "admin"
        )

    # NEW (Sep 2026 — profit tracking): store's cost price, admin-only.
    def get_purchase_price(self, obj):
        if not self._is_admin():
            return None
        return obj.purchase_price

    # NEW (Sep 2026 — profit tracking): price - purchase_price, admin-only.
    def get_profit(self, obj):
        if not self._is_admin() or obj.purchase_price is None:
            return None
        return obj.price - obj.purchase_price

    # ============================================================
    # NEW: available_stock = total_stock - reserved_stock
    # ============================================================
    def get_available_stock(self, obj):
        return obj.total_stock - obj.reserved_stock


# ============================================================
# UPDATED: LowStockProductSerializer with new stock fields
# ============================================================
class LowStockProductSerializer(ProductListSerializer):
    """
    FIX (Frontend bug report, Sep 2026): this used to be a tiny standalone
    serializer with only id/name/stock fields/threshold. The admin Products
    page uses /products/low-stock/ when Status = "Low Stock" is selected and
    renders the rows exactly like the normal product list, so the missing
    price / category / primary_image / sku / is_active made every row show
    Rs. 0, no category, no image and "No" under On Website.

    It now inherits everything from ProductListSerializer (price, category,
    primary_image, sku, is_active, purchase_price, profit, in_stock, ...) and
    keeps low_stock_threshold on top. The original fields — id, name,
    total_stock, reserved_stock, available_stock, low_stock_threshold — are
    all still present, so the dashboard low-stock widget keeps working.
    """

    class Meta(ProductListSerializer.Meta):
        fields = list(ProductListSerializer.Meta.fields) + [
            "low_stock_threshold",
        ]


# ============================================================
# UPDATED: ProductDetailSerializer with new stock fields
# ============================================================
class ProductDetailSerializer(serializers.ModelSerializer):
    images = ProductImageSerializer(many=True, read_only=True)
    category = serializers.SerializerMethodField()

    # ============================================================
    # NEW: available_stock computed field
    # ============================================================
    available_stock = serializers.SerializerMethodField()

    # NEW: rating + sales info for the product detail page (star rating
    # header, "Based on N reviews" bar chart, "4,120 sold" line).
    average_rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()
    total_sold = serializers.SerializerMethodField()

    # NEW (Sep 2026 — profit tracking): both admin-only, null for a
    # customer request — same shared-serializer situation as
    # ProductListSerializer (used by both the public detail page and the
    # admin panel's product edit page).
    purchase_price = serializers.SerializerMethodField()
    profit = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "description",
            "price",
            "original_price",
            # NEW (Sep 2026 — profit tracking): admin-only, null for customers
            "purchase_price",
            "profit",
            # ============================================================
            # NEW: Replace single 'stock' with three fields
            # ============================================================
            "total_stock",
            "reserved_stock",
            "available_stock",
            "in_stock",
            "sku",
            "category",
            "is_active",
            "low_stock_threshold",
            "publish_at",
            "images",
            # NEW
            "average_rating",
            "review_count",
            "total_sold",
            "created_at",
            "updated_at",
        ]

    # Returns category details for the product detail page.
    def get_category(self, obj):
        if not obj.category:
            return None

        return {
            "id": obj.category.id,
            "name": obj.category.name,
        }

    # ============================================================
    # NEW: available_stock = total_stock - reserved_stock
    # ============================================================
    def get_available_stock(self, obj):
        return obj.total_stock - obj.reserved_stock

    # NEW (Sep 2026 — profit tracking): True only for an authenticated
    # admin making the request.
    def _is_admin(self):
        request = self.context.get("request")
        return bool(
            request
            and request.user
            and request.user.is_authenticated
            and getattr(request.user, "role", None) == "admin"
        )

    # NEW (Sep 2026 — profit tracking): store's cost price, admin-only.
    def get_purchase_price(self, obj):
        if not self._is_admin():
            return None
        return obj.purchase_price

    # NEW (Sep 2026 — profit tracking): price - purchase_price, admin-only.
    def get_profit(self, obj):
        if not self._is_admin() or obj.purchase_price is None:
            return None
        return obj.price - obj.purchase_price

    # NEW: average of every active, non-deleted review's rating.
    # Rounded to 1 decimal place (e.g. 4.8), 0.0 when there are no
    # reviews yet.
    def get_average_rating(self, obj):
        avg = obj.reviews.filter(
            is_active=True, is_delete=False
        ).aggregate(avg=Avg("rating"))["avg"]
        return round(avg, 1) if avg is not None else 0.0

    # NEW: count of active, non-deleted reviews — the "2,450" in
    # "4.8 (2,450 reviews)".
    def get_review_count(self, obj):
        return obj.reviews.filter(is_active=True, is_delete=False).count()

    # NEW: total units sold, summed across every ProductStats row for
    # this product (ProductStats already tracks this daily for the
    # analytics dashboard — this just totals it for the product page).
    def get_total_sold(self, obj):
        total = obj.stats.aggregate(total=Sum("total_sold"))["total"]
        return total or 0


# ============================================================
# UPDATED: ProductCreateUpdateSerializer with new stock fields
# ============================================================
class ProductCreateUpdateSerializer(serializers.ModelSerializer):
    # NEW (Production SKU validation spec, Sep 2026): SKU is now
    # mandatory on every create/update — allow_blank=False rejects "" up
    # front. trim_whitespace=False because validate_sku() below does its
    # own trimming as an explicit, visible step (matching the stated
    # spec) rather than relying on DRF's default silent trim.
    sku = serializers.CharField(
        required=True,
        allow_blank=False,
        allow_null=False,
        trim_whitespace=False,
        max_length=SKU_MAX_LENGTH,
        error_messages={
            "required": "SKU is required.",
            "blank": "SKU is required.",
            "null": "SKU is required.",
            "max_length": f"SKU cannot be longer than {SKU_MAX_LENGTH} characters.",
        },
    )
    category_id = serializers.IntegerField(write_only=True, required=False)
    stock_to_add = serializers.IntegerField(
        write_only=True,
        required=False,
        default=0,
        min_value=0,
    )

    # FIX (Frontend Bug Report — Create Product stock always 0, Sep 2026):
    # the frontend's "Starting Quantity" field is sent as "stock" in the
    # POST payload, but this serializer only ever declared "total_stock"
    # — DRF silently drops any payload key that isn't a declared field
    # (no error raised), so total_stock was never set from the request
    # and fell back to the model's own default of 0 on every create.
    # "stock" is now accepted as a write-only alias. If a caller sends
    # "total_stock" directly that value still wins unchanged — "stock" is
    # only used as a fallback in create()/update() below when
    # total_stock wasn't provided at all, so nothing that already worked
    # (e.g. Update Product, Adjust Stock) is affected.
    stock = serializers.IntegerField(
        write_only=True,
        required=False,
        min_value=0,
    )

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "description",
            "price",
            "original_price",
            # NEW (Sep 2026 — profit tracking): admin-only cost price
            "purchase_price",
            # ============================================================
            # NEW: total_stock and reserved_stock
            # ============================================================
            "total_stock",
            "reserved_stock",
            "stock_to_add",
            "stock",
            "sku",
            "category",
            "category_id",
            "is_active",
            "low_stock_threshold",
            "publish_at",
        ]
        read_only_fields = ["id"]

    # NEW (Production SKU validation spec, Sep 2026): full validation
    # pipeline, applied in this order —
    # 1. Trim leading/trailing spaces; reject internal spaces
    # 2. Convert to uppercase before every further check and before saving
    # 3. Length: 3-25 characters
    # 4. Allowed characters + must start/end with a letter or number
    #    (regex) — this also structurally guarantees at least one
    #    alphanumeric character exists, since start/end can't both be
    #    "-"/"_"
    # 5. No consecutive special characters ("--", "__", "-_", "_-")
    # 6. Not a reserved word (NULL / TEST / ADMIN)
    # 7. Unique among still-active (is_delete=False) products — a
    #    soft-deleted product's SKU no longer blocks reuse
    def validate_sku(self, value):
        # 1) Trim; reject internal spaces
        trimmed = value.strip()

        if not trimmed:
            raise serializers.ValidationError("SKU is required.")

        if " " in trimmed:
            raise serializers.ValidationError(
                "SKU cannot contain spaces."
            )

        # 2) Uppercase
        value = trimmed.upper()

        # 3) Length
        if len(value) < SKU_MIN_LENGTH:
            raise serializers.ValidationError(
                f"SKU must be at least {SKU_MIN_LENGTH} characters long."
            )

        if len(value) > SKU_MAX_LENGTH:
            raise serializers.ValidationError(
                f"SKU cannot be longer than {SKU_MAX_LENGTH} characters."
            )

        # 4) Allowed characters + must start/end with a letter or number
        if not SKU_REGEX.match(value):
            raise serializers.ValidationError(
                "SKU must start and end with a letter or number, and can "
                "only contain uppercase letters, numbers, hyphens (-), "
                "and underscores (_)."
            )

        # 5) No consecutive special characters
        if any(bad in value for bad in ("--", "__", "-_", "_-")):
            raise serializers.ValidationError(
                "SKU cannot contain consecutive special characters "
                "(e.g. '--', '__')."
            )

        # 6) Reserved words
        if value in RESERVED_SKUS:
            raise serializers.ValidationError(
                f"'{value}' is a reserved word and cannot be used as a SKU."
            )

        # 7) Uniqueness among active products
        qs = Product.objects.filter(sku=value, is_delete=False)

        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise serializers.ValidationError(
                "A product with this SKU already exists."
            )

        return value

    # Creates a new product and assigns it to the logged-in user's store.
    def create(self, validated_data):
        request = self.context["request"]
        # UPDATED (v4.0): related_name changed from 'stores' to
        # 'administered_stores' now that a store has multiple, equal
        # admins (Store.admins M2M) instead of a single owner.
        validated_data["store"] = request.user.administered_stores.first()

        category_id = validated_data.pop("category_id", None)
        if category_id:
            validated_data["category_id"] = category_id

        validated_data.pop("stock_to_add", None)

        # FIX (Frontend Bug Report, Sep 2026): map the "stock" alias onto
        # total_stock as a fallback — only when total_stock itself wasn't
        # provided, so an explicit total_stock always takes priority.
        incoming_stock = validated_data.pop("stock", None)
        if "total_stock" not in validated_data and incoming_stock is not None:
            validated_data["total_stock"] = incoming_stock

        # Ensure reserved_stock is 0 by default
        validated_data.setdefault("reserved_stock", 0)

        return super().create(validated_data)

    # Prevents duplicate product names — only among products that are
    # still "alive" (is_delete=False). A soft-deleted product's name no
    # longer blocks a new product from reusing it, matching the
    # is_delete=False condition on the DB-level unique constraint.
    def validate_name(self, value):
        qs = Product.objects.filter(name=value, is_delete=False)

        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise serializers.ValidationError(
                "A product with this name already exists."
            )

        return value

    def validate(self, data):
        price = data.get(
            "price",
            getattr(self.instance, "price", None),
        )

        original_price = data.get(
            "original_price",
            getattr(self.instance, "original_price", None),
        )

        if (
            price is not None
            and original_price is not None
            and price > original_price
        ):
            raise serializers.ValidationError(
                {
                    "price": (
                        "Actual price cannot be greater than "
                        "original price because this would create "
                        "a negative discount."
                    )
                }
            )

        # NEW (Sep 2026 — profit tracking): purchase_price is the store's
        # cost price, only used to calculate profit (price -
        # purchase_price) for the admin. Just needs to be non-negative —
        # unlike original_price/price above, price is allowed to be lower
        # than purchase_price (admin may knowingly sell at a loss, e.g.
        # clearance stock), so that isn't blocked here.
        purchase_price = data.get(
            "purchase_price",
            getattr(self.instance, "purchase_price", None),
        )
        if purchase_price is not None and purchase_price < 0:
            raise serializers.ValidationError(
                {"purchase_price": "Purchase price cannot be negative."}
            )

        # ============================================================
        # NEW: Validate reserved_stock doesn't exceed total_stock
        # ============================================================
        total_stock = data.get("total_stock")
        reserved_stock = data.get("reserved_stock", 0)

        if total_stock is not None and reserved_stock > total_stock:
            raise serializers.ValidationError(
                {
                    "reserved_stock": (
                        "Reserved stock cannot exceed total stock."
                    )
                }
            )

        return data

    # Updates existing product information.
    # NEW (Production SKU validation spec, Sep 2026): SKU is immutable
    # after creation — a normal update request cannot change it. To
    # deliberately override this (rare, admin-only correction), the
    # request must include "admin_override_sku": true alongside the new
    # sku value; without that flag, changing sku on update is rejected
    # with a 400 even though every other field updates normally.
    def update(self, instance, validated_data):
        new_sku = validated_data.get("sku")

        if new_sku is not None and new_sku != instance.sku:
            request = self.context.get("request")
            override = False

            if request is not None:
                override_raw = request.data.get("admin_override_sku", False)
                override = str(override_raw).strip().lower() in ("true", "1", "yes")

            if not override:
                raise serializers.ValidationError(
                    {
                        "sku": (
                            "SKU cannot be changed after the product is "
                            "created. To override this, resend the "
                            "request with admin_override_sku: true."
                        )
                    }
                )

        # NOTE: 'stock_to_add' on this endpoint is kept working for backward
        # compatibility, but the frontend should no longer send it once a
        # product already exists — stock changes after creation now go through
        # the dedicated, atomic POST /api/v1/products/{id}/stock/adjust/
        # endpoint instead, which is safe under concurrent checkout/cancel
        # activity. This PUT/update path is NOT safe for concurrent stock
        # changes since it reads instance.stock in Python before saving.
        stock_to_add = validated_data.pop("stock_to_add", 0)

        # FIX (Frontend Bug Report, Sep 2026): same "stock" alias handling
        # as create() — pulled out before the loop below so it maps onto
        # total_stock instead of silently landing on the deprecated
        # Product.stock field via setattr. Only used as a fallback when
        # total_stock itself wasn't sent.
        incoming_stock = validated_data.pop("stock", None)
        if "total_stock" not in validated_data and incoming_stock is not None:
            validated_data["total_stock"] = incoming_stock

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        # ============================================================
        # NEW: stock_to_add now adds to total_stock only (not reserved)
        # as per PDF Part 2 Item 5
        # ============================================================
        if stock_to_add:
            instance.total_stock += stock_to_add

        instance.save()

        return instance


# Returns product price and stock change history.
class ProductHistorySerializer(serializers.ModelSerializer):
    changed_by_name = serializers.CharField(
        source="changed_by.name",
        read_only=True,
        default="System",
    )

    class Meta:
        model = ProductHistory
        fields = [
            "id",
            "changed_by_name",
            "old_price",
            "new_price",
            "old_stock",
            "new_stock",
            "reason",
            "created_at",
        ]


# NEW (stock race-condition fix): validates the request body for
# POST /api/v1/products/{id}/stock/adjust/. Only the 5 manual-adjustment
# reasons are accepted here — 'order_placed' / 'order_cancelled' are
# system-only reasons used internally by checkout/cancel flows and are
# never accepted from this endpoint.

# Validates manual stock adjustment requests.
class StockAdjustSerializer(serializers.Serializer):
    MANUAL_REASON_CHOICES = [
        "restock",
        "damaged",
        "correction",
        "return",
        "other",
    ]

    delta = serializers.IntegerField()
    reason = serializers.ChoiceField(choices=MANUAL_REASON_CHOICES)
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)

    # Prevents stock adjustment requests with zero quantity.
    def validate_delta(self, value):
        if value == 0:
            raise serializers.ValidationError("delta cannot be 0.")
        return value