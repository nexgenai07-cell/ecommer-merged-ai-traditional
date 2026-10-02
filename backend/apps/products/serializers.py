# PATH: apps/products/serializers.py

import re

from rest_framework import serializers
from django.db.models import Avg, Sum
from .models import Product, ProductImage, ProductVariant, ProductHistory, StockMovement
from .card_stats import CardStatsMixin


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
            # NEW (Oct 2026 — per-color images): "" for a general image,
            # otherwise the variant color it belongs to (e.g. "Black").
            "color",
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
class ProductListSerializer(CardStatsMixin, serializers.ModelSerializer):
    primary_image = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()

    # NEW (Oct 2026 - storefront product cards): same three public figures
    # as Get Single Product, same rules (approved + non-deleted reviews,
    # real paid orders) - see card_stats.py. Plain JSON numbers, returned to
    # guests and customers too. The list views annotate them in the
    # queryset, so a page of products runs no per-product queries.
    average_rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()
    total_sold = serializers.SerializerMethodField()

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
    # NEW (Sep 2026 — markup/margin calculation): admin-only, null for
    # customers. Both pulled from Product.markup_percent /
    # Product.profit_margin_percent (apps/products/models.py) so the
    # % math lives in exactly one place — see that model for the
    # markup-vs-margin distinction (cost-based vs price-based %).
    markup_percent = serializers.SerializerMethodField()
    profit_percent = serializers.SerializerMethodField()
    profit_margin_percent = serializers.SerializerMethodField()

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
            "markup_percent",
            "profit_percent",
            "profit_margin_percent",
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
            "average_rating",
            "review_count",
            "total_sold",
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

    # UPDATED (Sep 2026 — markup/margin calculation): now delegates to
    # Product.markup_amount instead of repeating "price - purchase_price"
    # here, so this and ProductDetailSerializer can never drift apart.
    def get_profit(self, obj):
        if not self._is_admin():
            return None
        return obj.markup_amount

    # NEW (Sep 2026 — markup/margin calculation): % on cost price —
    # see Product.markup_percent in apps/products/models.py.
    def get_markup_percent(self, obj):
        if not self._is_admin():
            return None
        return obj.markup_percent

    # NEW (Sep 2026 — profit concepts): Profit % (profit / cost x 100) —
    # same formula as markup_percent, see Product.profit_percent.
    def get_profit_percent(self, obj):
        if not self._is_admin():
            return None
        return obj.profit_percent

    # NEW (Sep 2026 — markup/margin calculation): % on selling price —
    # see Product.profit_margin_percent in apps/products/models.py.
    def get_profit_margin_percent(self, obj):
        if not self._is_admin():
            return None
        return obj.profit_margin_percent

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
# NEW (Oct 2026 — product variants)
# ============================================================
HEX_COLOR_REGEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _request_is_admin(context):
    request = context.get("request")
    return bool(
        request
        and request.user
        and request.user.is_authenticated
        and getattr(request.user, "role", None) == "admin"
    )


# NEW (Oct 2026 — variant pricing): the "-20% OFF" badge value — how far
# price is below original_price, as a whole percent. 0 when there is no
# original_price or it is not higher than the price.
def _discount_percent(price, original_price):
    if price is None or original_price is None or original_price <= 0:
        return 0
    if original_price <= price:
        return 0
    return int(round((original_price - price) / original_price * 100))


# Read-only shape of one variant (Black / Large, its own price + stock).
# total_stock / reserved_stock / is_active are admin-only — a customer
# only ever gets available_stock + in_stock, same idea as price/profit
# fields elsewhere in this file.
class ProductVariantSerializer(serializers.ModelSerializer):
    label = serializers.CharField(read_only=True)
    discount_percent = serializers.SerializerMethodField()
    available_stock = serializers.SerializerMethodField()
    in_stock = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = [
            "id",
            "color",
            "color_hex",
            "size",
            "label",
            "price",
            "original_price",
            "discount_percent",
            "available_stock",
            "in_stock",
            "total_stock",
            "reserved_stock",
            "is_active",
        ]

    def get_discount_percent(self, obj):
        return _discount_percent(obj.price, obj.original_price)

    def get_available_stock(self, obj):
        return obj.total_stock - obj.reserved_stock

    def get_in_stock(self, obj):
        return obj.in_stock

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not _request_is_admin(self.context):
            for key in ("total_stock", "reserved_stock", "is_active"):
                data.pop(key, None)
        return data


# Admin create / update of one variant. The product it belongs to must
# be passed in the serializer context as context["product"].
#
# total_stock is only accepted as the STARTING stock on create; after
# that, stock changes go through the atomic variant stock-adjust
# endpoint (same reason Product has /stock/adjust/ — a plain update
# is not safe against concurrent checkouts).
class ProductVariantWriteSerializer(serializers.ModelSerializer):
    color = serializers.CharField(
        required=False, allow_blank=True, max_length=50
    )
    size = serializers.CharField(
        required=False, allow_blank=True, max_length=50
    )
    color_hex = serializers.CharField(
        required=False, allow_blank=True, max_length=7
    )
    total_stock = serializers.IntegerField(required=False, min_value=0)

    class Meta:
        model = ProductVariant
        fields = [
            "id",
            "color",
            "color_hex",
            "size",
            "price",
            "original_price",
            "total_stock",
            "is_active",
        ]
        read_only_fields = ["id"]

    def validate_color_hex(self, value):
        value = (value or "").strip()
        if value and not HEX_COLOR_REGEX.match(value):
            raise serializers.ValidationError(
                "Color hex must look like #RRGGBB, e.g. #000000."
            )
        return value.upper()

    def validate_price(self, value):
        if value <= 0:
            raise serializers.ValidationError("Price must be greater than 0.")
        return value

    def validate(self, data):
        product = self.context["product"]
        instance = self.instance

        def _final(field):
            if field in data:
                return (data[field] or "").strip()
            return (getattr(instance, field, "") or "").strip() if instance else ""

        color = _final("color")
        size = _final("size")
        data["color"] = color
        data["size"] = size

        if not (color or size):
            raise serializers.ValidationError(
                "A variant needs at least a color or a size/kit."
            )

        others = product.variants.filter(is_delete=False)
        if instance is not None:
            others = others.exclude(pk=instance.pk)

        if others.exists():
            uses_color = others.exclude(color="").exists()
            uses_size = others.exclude(size="").exists()

            if uses_color and not color:
                raise serializers.ValidationError(
                    {"color": "Color is required — this product's other variants have colors."}
                )
            if uses_size and not size:
                raise serializers.ValidationError(
                    {"size": "Size/kit is required — this product's other variants have sizes/kits."}
                )
            if color and not uses_color:
                raise serializers.ValidationError(
                    {"color": "This product's other variants have no color, so this one can't have a color either."}
                )
            if size and not uses_size:
                raise serializers.ValidationError(
                    {"size": "This product's other variants have no size/kit, so this one can't have a size/kit either."}
                )

            if others.filter(color__iexact=color, size__iexact=size).exists():
                raise serializers.ValidationError(
                    "A variant with this color and size/kit already exists for this product."
                )

        price = data.get("price", getattr(instance, "price", None))
        original_price = data.get(
            "original_price", getattr(instance, "original_price", None)
        )
        if (
            price is not None
            and original_price is not None
            and price > original_price
        ):
            raise serializers.ValidationError(
                {
                    "price": (
                        "Actual price cannot be greater than original "
                        "price because this would create a negative discount."
                    )
                }
            )

        if instance is not None and "total_stock" in data:
            if data["total_stock"] != instance.total_stock:
                raise serializers.ValidationError(
                    {
                        "total_stock": (
                            "Stock can't be changed here. Use the variant "
                            "stock adjust endpoint instead."
                        )
                    }
                )
            data.pop("total_stock")

        return data


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

    # NEW (Oct 2026 — product variants): color / size-kit options, each
    # variant with its own price + stock. has_variants=True means the
    # customer MUST pick a variant to add this product to the cart.
    has_variants = serializers.SerializerMethodField()
    variants = serializers.SerializerMethodField()
    variant_options = serializers.SerializerMethodField()
    # NEW (Oct 2026 — variant pricing): "-20% OFF" badge for the product's
    # own price. Each variant carries its own discount_percent too.
    discount_percent = serializers.SerializerMethodField()

    # NEW (Sep 2026 — profit tracking): both admin-only, null for a
    # customer request — same shared-serializer situation as
    # ProductListSerializer (used by both the public detail page and the
    # admin panel's product edit page).
    purchase_price = serializers.SerializerMethodField()
    profit = serializers.SerializerMethodField()
    # NEW (Sep 2026 — markup/margin calculation): admin-only, null for
    # customers. See Product.markup_percent / profit_margin_percent in
    # apps/products/models.py for the cost-based-vs-price-based-% distinction.
    markup_percent = serializers.SerializerMethodField()
    profit_percent = serializers.SerializerMethodField()
    profit_margin_percent = serializers.SerializerMethodField()

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
            "markup_percent",
            "profit_percent",
            "profit_margin_percent",
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
            # NEW (Oct 2026 — product variants)
            "has_variants",
            "variants",
            "variant_options",
            "discount_percent",
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

    # UPDATED (Sep 2026 — markup/margin calculation): now delegates to
    # Product.markup_amount instead of repeating "price - purchase_price"
    # here, so this and ProductListSerializer can never drift apart.
    def get_profit(self, obj):
        if not self._is_admin():
            return None
        return obj.markup_amount

    # NEW (Sep 2026 — markup/margin calculation): % on cost price —
    # see Product.markup_percent in apps/products/models.py.
    def get_markup_percent(self, obj):
        if not self._is_admin():
            return None
        return obj.markup_percent

    # NEW (Sep 2026 — profit concepts): Profit % (profit / cost x 100) —
    # same formula as markup_percent, see Product.profit_percent.
    def get_profit_percent(self, obj):
        if not self._is_admin():
            return None
        return obj.profit_percent

    # NEW (Sep 2026 — markup/margin calculation): % on selling price —
    # see Product.profit_margin_percent in apps/products/models.py.
    def get_profit_margin_percent(self, obj):
        if not self._is_admin():
            return None
        return obj.profit_margin_percent

    # NEW (Oct 2026 — product variants): True when the product has any
    # non-deleted variant -> the customer must choose one.
    def get_has_variants(self, obj):
        return obj.has_variants

    def get_discount_percent(self, obj):
        return _discount_percent(obj.price, obj.original_price)

    # Customers only see active variants; an admin sees all (including
    # inactive) so the edit page can re-activate them.
    def _visible_variants(self, obj):
        qs = obj.variants.filter(is_delete=False)
        if not self._is_admin():
            qs = qs.filter(is_active=True)
        return qs.order_by("id")

    def get_variants(self, obj):
        return ProductVariantSerializer(
            self._visible_variants(obj),
            many=True,
            context=self.context,
        ).data

    # The unique colors and sizes/kits of the visible variants, in the
    # order they were created — ready for the swatch row ("Color") and
    # the pill row ("Size / Kit") on the product page.
    def get_variant_options(self, obj):
        colors, sizes = [], []
        seen_colors, seen_sizes = set(), set()

        # NEW (Oct 2026 — per-color images): each color also carries its
        # own images (image URLs, that color's primary first). When a
        # color has none, the frontend shows the product's general images
        # (the entries in "images" whose color is "").
        images_by_color = {}
        for img in obj.images.all():
            if not img.color or not img.image:
                continue
            images_by_color.setdefault(img.color.lower(), []).append(img)

        def _urls(color):
            group = images_by_color.get(color.lower(), [])
            group = sorted(group, key=lambda i: not i.is_primary)
            return [i.image.url.replace("http://", "https://") for i in group]

        for variant in self._visible_variants(obj):
            if variant.color and variant.color.lower() not in seen_colors:
                seen_colors.add(variant.color.lower())
                colors.append(
                    {
                        "name": variant.color,
                        "hex": variant.color_hex,
                        "images": _urls(variant.color),
                    }
                )
            if variant.size and variant.size.lower() not in seen_sizes:
                seen_sizes.add(variant.size.lower())
                sizes.append(variant.size)

        return {"colors": colors, "sizes": sizes}

    # UPDATED (Oct 2026 — rating consistency fix): average of every
    # APPROVED, non-deleted review's rating. Previously this filtered on
    # is_active=True (a deprecated field), so pending/rejected reviews
    # leaked into the number while review_views._get_rating_summary only
    # counted status="approved" — the two blocks on the detail page could
    # disagree. Both now use the same rule.
    # Rounded to 1 decimal place (e.g. 4.8), 0.0 when there are no
    # approved reviews yet.
    def get_average_rating(self, obj):
        avg = obj.reviews.filter(
            status="approved", is_delete=False
        ).aggregate(avg=Avg("rating"))["avg"]
        return round(avg, 1) if avg is not None else 0.0

    # UPDATED (Oct 2026 — rating consistency fix): count of APPROVED,
    # non-deleted reviews — the "2,450" in "4.8 (2,450 reviews)". Same
    # rule as review_views._get_rating_summary.
    def get_review_count(self, obj):
        return obj.reviews.filter(status="approved", is_delete=False).count()

    # UPDATED (Oct 2026 — real sales count): total units sold, now
    # counted straight from real orders instead of the ProductStats
    # table (nothing in apps/products ever wrote to ProductStats, so
    # the old number was effectively always 0).
    #
    # Sums OrderItem.quantity for this product, only over orders whose
    # status is in Order.REVENUE_STATUSES (confirmed / shipped /
    # out_for_delivery / delivered) — i.e. payment is confirmed. Same
    # single source of truth the revenue / customer-spent numbers use,
    # so pending_payment, on_hold, order_placed and cancelled
    # (refunded) orders never inflate "4,120 sold".
    #
    # Order is imported inside the method (not at the top of the file)
    # to avoid a circular import between apps.products and apps.orders.
    def get_total_sold(self, obj):
        from apps.orders.models import Order, OrderItem

        total = OrderItem.objects.filter(
            product=obj,
            order__status__in=Order.REVENUE_STATUSES,
        ).aggregate(total=Sum("quantity"))["total"]
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
    # NEW (Sep 2026 — purchase_price made compulsory): previously
    # optional (model field is null=True, blank=True — kept that way at
    # the DB level as a safety net, same reasoning as `sku` above), so
    # admin add/edit forms could silently submit no cost price at all.
    # Explicitly required here, same pattern as `sku`, so create/full
    # update (PUT) reject a missing/blank/null purchase_price.
    # UPDATED (Oct 2026): on update, a MISSING key is allowed when the
    # product already has a saved value (see __init__ below); a blank or
    # null key is still rejected.
    # A partial update (PATCH) is unaffected — DRF doesn't enforce
    # required=True fields during partial=True, so an unrelated PATCH
    # (e.g. toggling is_active) doesn't force re-sending purchase_price.
    purchase_price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=True,
        allow_null=False,
        error_messages={
            "required": "Purchase price is required.",
            "null": "Purchase price is required.",
            "invalid": "Enter a valid purchase price.",
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

    # NEW (Oct 2026 — admin edit form losing purchase_price): on UPDATE
    # (PUT), if the request does not send the `purchase_price` key at all
    # AND the product already has a purchase_price saved, the saved value
    # is kept instead of rejecting the request with "Purchase price is
    # required." (the frontend form can lose this field after an image
    # upload / stock update / live refresh).
    #
    # Rules that did NOT change:
    # - CREATE: purchase_price is still required.
    # - A key that IS sent but blank / null is still rejected (400) — see
    #   to_internal_value() below.
    # - An old product that has no purchase_price saved yet still has to
    #   get one on its next full update.
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = getattr(self, "instance", None)
        if (
            instance is not None
            and not isinstance(instance, (list, tuple))
            and getattr(instance, "purchase_price", None) is not None
        ):
            self.fields["purchase_price"].required = False

    def to_internal_value(self, data):
        # With required=False (set in __init__ above), DRF treats a blank
        # purchase_price in multipart/form-data as "missing" and would
        # silently keep the old value. A purchase_price key that is sent
        # but empty/null must still be an error, so it is checked here on
        # the raw input, before DRF's own field handling.
        if self.instance is not None and "purchase_price" in data:
            raw = data.get("purchase_price")
            if raw is None or (isinstance(raw, str) and raw.strip() == ""):
                raise serializers.ValidationError(
                    {"purchase_price": ["Purchase price is required."]}
                )
        return super().to_internal_value(data)

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

        # NEW (Oct 2026 — product variants): a product with variants has
        # no stock number of its own (it's the sum of its variants), so
        # this endpoint can't change it. An edit form that just re-sends
        # the current total_stock unchanged is fine and is ignored; an
        # actual change (or stock_to_add) is rejected with a pointer to
        # the variant stock endpoint.
        if instance.has_variants:
            requested_total = validated_data.pop("total_stock", None)
            validated_data.pop("reserved_stock", None)

            if stock_to_add or (
                requested_total is not None
                and requested_total != instance.total_stock
            ):
                raise serializers.ValidationError(
                    {
                        "total_stock": (
                            "This product has variants, so its stock is the "
                            "sum of its variants' stock. Change stock on the "
                            "variants instead."
                        )
                    }
                )

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