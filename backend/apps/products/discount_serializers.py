# PATH: apps/products/discount_serializers.py

from datetime import time

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

    # Prevents duplicate discount codes — only among discounts that are
    # still "alive" (is_delete=False). A soft-deleted discount's code no
    # longer blocks a new discount from reusing it, matching the
    # is_delete=False condition on the DB-level unique constraint (see
    # products/migrations/0018_discount_code_reuse_after_soft_delete.py).
    def validate_code(self, value):
        qs = Discount.objects.filter(code=value, is_delete=False)

        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise serializers.ValidationError(
                "A discount with this code already exists."
            )

        return value

    # Validates the discount's date range.
    #
    # FIX (bug report, Sep 2026) — three issues fixed here together:
    #
    # 1. Past start_date: creating (or editing) a discount with a
    #    start_date before today's date was previously allowed with no
    #    check at all. Now blocked, whenever "start_date" is actually
    #    being submitted. Compared by DATE only (not exact time), so a
    #    discount starting "today" is still allowed even if the picked
    #    time is earlier than the current moment.
    #
    # 2. Same-day / few-hours discounts couldn't be created: when the
    #    frontend sends only a DATE for end_date (no time), Django was
    #    storing it as 00:00:00 (midnight / start of that day). So a
    #    discount with start_date = today 10:00 AM and end_date = today
    #    (00:00:00) looked like "end is before start" and got rejected
    #    by the check below.
    #
    # 3. Discounts expiring at 12:00 AM instead of 11:59 PM: for the
    #    same reason as #2 — an end_date of "today" stored as midnight
    #    means the coupon is technically already "expired" the moment
    #    the day starts, not when it ends.
    #
    # Fix: if end_date's time component is exactly midnight (meaning
    # only a date was picked, no specific time), we treat that as
    # "valid through the end of that day" and bump it to 23:59:59.999999
    # before running the start < end check and saving. If the frontend
    # DOES send a specific end time (e.g. "today, 6:00 PM" for an
    # hours-long discount), that exact time is respected and left as-is.
    def validate(self, data):
        now = timezone.now()

        start = data.get(
            "start_date",
            getattr(self.instance, "start_date", None),
        )
        end = data.get(
            "end_date",
            getattr(self.instance, "end_date", None),
        )

        # --- Fix 1: no past start dates ---
        if "start_date" in data and start:
            if timezone.localtime(start).date() < timezone.localtime(now).date():
                raise serializers.ValidationError(
                    {"start_date": "Start date cannot be in the past."}
                )

        # --- Fix 2 & 3: end_date with no time = end of that day ---
        if end is not None:
            local_end = timezone.localtime(end)
            if local_end.time() == time.min:
                end = local_end.replace(
                    hour=23, minute=59, second=59, microsecond=999999
                )
                data["end_date"] = end

        if start and end and start >= end:
            raise serializers.ValidationError(
                "End date must be after start date."
            )

        return data

    # Automatically assigns the logged-in admin's store
    # before creating the discount.
    def create(self, validated_data):
        request = self.context["request"]
        # UPDATED (v4.0): related_name changed from 'stores' to
        # 'administered_stores' — see Store.admins M2M.
        validated_data["store"] = request.user.administered_stores.first()
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