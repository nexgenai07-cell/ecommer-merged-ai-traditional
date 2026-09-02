from rest_framework import serializers
from .models import Product, ProductImage, ProductHistory, StockMovement


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

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "price",
            "original_price",
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

    # ============================================================
    # NEW: available_stock = total_stock - reserved_stock
    # ============================================================
    def get_available_stock(self, obj):
        return obj.total_stock - obj.reserved_stock


# ============================================================
# UPDATED: LowStockProductSerializer with new stock fields
# ============================================================
class LowStockProductSerializer(serializers.ModelSerializer):
    available_stock = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "total_stock",
            "reserved_stock",
            "available_stock",
            "low_stock_threshold",
        ]

    def get_available_stock(self, obj):
        return obj.total_stock - obj.reserved_stock


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

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "description",
            "price",
            "original_price",
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


# ============================================================
# UPDATED: ProductCreateUpdateSerializer with new stock fields
# ============================================================
class ProductCreateUpdateSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(required=False, allow_blank=True)
    category_id = serializers.IntegerField(write_only=True, required=False)
    stock_to_add = serializers.IntegerField(
        write_only=True,
        required=False,
        default=0,
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
            # ============================================================
            # NEW: total_stock and reserved_stock
            # ============================================================
            "total_stock",
            "reserved_stock",
            "stock_to_add",
            "sku",
            "category",
            "category_id",
            "is_active",
            "low_stock_threshold",
            "publish_at",
        ]
        read_only_fields = ["id"]

    # Validates that every SKU remains unique.
    def validate_sku(self, value):
        if not value:
            return value

        qs = Product.objects.filter(sku=value)

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
        validated_data["store"] = request.user.stores.first()

        category_id = validated_data.pop("category_id", None)
        if category_id:
            validated_data["category_id"] = category_id

        validated_data.pop("stock_to_add", None)

        # Ensure reserved_stock is 0 by default
        validated_data.setdefault("reserved_stock", 0)

        return super().create(validated_data)

    # Prevents duplicate product names.
    def validate_name(self, value):
        qs = Product.objects.filter(name=value)

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
    def update(self, instance, validated_data):
        # NOTE: 'stock_to_add' on this endpoint is kept working for backward
        # compatibility, but the frontend should no longer send it once a
        # product already exists — stock changes after creation now go through
        # the dedicated, atomic POST /api/v1/products/{id}/stock/adjust/
        # endpoint instead, which is safe under concurrent checkout/cancel
        # activity. This PUT/update path is NOT safe for concurrent stock
        # changes since it reads instance.stock in Python before saving.
        stock_to_add = validated_data.pop("stock_to_add", 0)

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