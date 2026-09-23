# PATH: apps/orders/customer_serializers.py

from decimal import Decimal

from rest_framework import serializers
from .models import Customer, Order


# Converts customer information into API responses for the admin panel.
class CustomerAdminSerializer(serializers.ModelSerializer):
    user_email = serializers.CharField(
        source="user.email",
        read_only=True,
    )
    total_orders = serializers.SerializerMethodField()
    total_spent = serializers.SerializerMethodField()

    # NEW (Sep 2026 — Admin Customers page missing phone bug): phone used
    # to read straight off Customer.phone — a one-time snapshot copied
    # from User.phone the moment the Customer row was first created
    # (either by the new-user signal at registration, or
    # get_or_create_customer() on the customer's first order — see
    # apps/users/signals.py). If the account's phone was empty at that
    # exact moment (e.g. Google sign-up, which never collects a phone,
    # or the phone was only added afterward via profile edit), this
    # column stayed permanently blank even once the account's real phone
    # was set — nothing re-syncs Customer.phone after creation.
    # obj.user.phone is the live, always-current value for any customer
    # who has an account, so it's preferred here; obj.phone remains the
    # fallback for a guest-checkout customer (user=None — see
    # Customer.user's null=True/blank=True comment in models.py).
    phone = serializers.SerializerMethodField()

    class Meta:
        model = Customer
        fields = [
            "id",
            "user",
            "user_email",
            "name",
            "phone",
            "email",
            "address",
            "total_orders",
            "total_spent",
            "created_at",
        ]

    # NEW (Sep 2026 — Admin Customers page missing phone bug)
    def get_phone(self, obj):
        if obj.user and obj.user.phone:
            return obj.user.phone
        return obj.phone

    # Returns total number of orders that actually count as "placed" —
    # i.e. the customer has paid for them.
    #
    # FIX (Sep 2026 — Total Spent / Revenue consistency): previously
    # excluded only "cancelled" + "pending_payment", which still counted
    # "on_hold" orders (payment under review, not confirmed yet) as
    # placed. Now uses Order.REVENUE_STATUSES (confirmed / shipped /
    # out_for_delivery / delivered) as an explicit include-list instead,
    # so this can never silently start counting a new not-yet-paid
    # status again if one gets added later.
    def get_total_orders(self, obj):
        return obj.orders.filter(status__in=Order.REVENUE_STATUSES).count()

    # Returns total amount actually spent — only orders in
    # Order.REVENUE_STATUSES count (confirmed / shipped /
    # out_for_delivery / delivered). Cancelled orders are excluded, which
    # also correctly excludes refunds: a refund always sets
    # order.status = "cancelled" (there's no separate "refunded" order
    # status — see AdminOrderStatusUpdateView), so a refunded order's
    # amount is automatically removed from total_spent the moment the
    # refund happens.
    #
    # FIX (Cross-check, Sep 2026 — PDF Part 4): sum()'s default start
    # value is Python int 0, so when a customer has no qualifying orders
    # this returned plain int 0 (JSON number) instead of the
    # spec-required string "0.00" — meaning the field's JSON type
    # depended on whether the customer had orders. Decimal("0.00") as an
    # explicit start value makes this always return a Decimal (=> always
    # a JSON string).
    def get_total_spent(self, obj):
        return sum(
            (
                order.total_amount
                for order in obj.orders.filter(status__in=Order.REVENUE_STATUSES)
            ),
            Decimal("0.00"),
        )