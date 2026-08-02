from rest_framework import serializers
from django.utils import timezone

from .models import Discount

# Serializes discount data for creating, updating,
# and retrieving discount coupons.
class DiscountSerializer(serializers.ModelSerializer):
    class Meta:
        model = Discount
        fields = [
            "id",
            "code",
            "type",
            "value",
            "min_order_amount",
            "start_date",
            "end_date",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
        ]
        # NOTE (ticket: Discount Delete — soft delete): "is_active" is
        # intentionally NOT in read_only_fields. This is what makes the
        # RESTORE flow work with no new endpoint — PUT /discounts/{id}/
        # with {"is_active": true} in the body reactivates a
        # soft-deleted coupon via this serializer. Confirmed already
        # correct; left as-is.

    # Validates that the discount end date
    # is later than the start date.
    def validate(self, data):
        start = data.get(
            "start_date",
            getattr(self.instance, "start_date", None),
        )
        end = data.get(
            "end_date",
            getattr(self.instance, "end_date", None),
        )

        if start and end and start >= end:
            raise serializers.ValidationError(
                "End date must be after start date."
            )

        return data

    # Automatically assigns the logged-in admin's store
    # before creating the discount.
    def create(self, validated_data):
        request = self.context["request"]
        validated_data["store"] = request.user.stores.first()
        return super().create(validated_data)


# Validates coupon codes during checkout
# before applying any discount.
class DiscountValidateSerializer(serializers.Serializer):
    """
    Used for POST /api/v1/discounts/validate/
    """

    code = serializers.CharField()
    order_amount = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    # Checks whether the coupon exists, is active,
    # has not expired, and satisfies the minimum
    # order amount before allowing its use.
    #
    # NOTE (ticket: Discount Delete — soft delete): this query already
    # filters is_active=True, so a soft-deleted coupon (is_active=False)
    # already falls into the DoesNotExist branch below and is rejected
    # with "Invalid or inactive coupon code." Confirmed already correct;
    # no change needed here.
    def validate(self, data):
        try:
            # Only ACTIVE discounts are valid.
            discount = Discount.objects.get(
                code=data["code"],
                is_active=True,
                is_delete=False,
            )
        except Discount.DoesNotExist:
            raise serializers.ValidationError(
                {
                    "code": "Invalid or inactive coupon code."
                }
            )

        now = timezone.now()

        if not (discount.start_date <= now <= discount.end_date):
            raise serializers.ValidationError(
                {
                    "code": "This coupon has expired or is not active yet."
                }
            )

        if (
            discount.min_order_amount
            and data["order_amount"] < discount.min_order_amount
        ):
            raise serializers.ValidationError(
                {
                    "order_amount": (
                        f"Minimum order amount of Rs. "
                        f"{discount.min_order_amount} required for this coupon."
                    )
                }
            )

        data["discount"] = discount
        return data