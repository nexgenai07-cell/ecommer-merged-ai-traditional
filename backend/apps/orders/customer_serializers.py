# PATH: apps/orders/customer_serializers.py

from rest_framework import serializers
from .models import Customer


# Converts customer information into API responses for the admin panel.
class CustomerAdminSerializer(serializers.ModelSerializer):
    user_email = serializers.CharField(
        source="user.email",
        read_only=True,
    )
    total_orders = serializers.SerializerMethodField()
    total_spent = serializers.SerializerMethodField()

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

    # Returns total number of REAL orders placed by the customer.
    # FIX (B57): "cancelled" ke sath "pending_payment" bhi exclude kiya —
    # pending_payment order ka matlab hai payment abhi complete hi nahi
    # hui, isliye woh "placed" order nahi ginna chahiye. Is fix ke baad
    # jin customers ke sirf pending orders hain unke liye ye 0 aayega.
    def get_total_orders(self, obj):
        return obj.orders.exclude(
            status__in=["cancelled", "pending_payment"]
        ).count()

    # Returns total amount actually spent — cancelled AND unpaid
    # (pending_payment) orders excluded, kyunke unka paisa kabhi actually
    # nahi aaya. FIX (B57): pehle sirf "cancelled" exclude hota tha, is
    # liye pending/unpaid orders ka amount lifetime value mein ghalat
    # tarah jud jata tha.
    def get_total_spent(self, obj):
        return sum(
            order.total_amount
            for order in obj.orders.exclude(
                status__in=["cancelled", "pending_payment"]
            )
        )