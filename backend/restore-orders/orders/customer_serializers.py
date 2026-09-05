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

    # Returns total number of orders placed by the customer.
    def get_total_orders(self, obj):
        return obj.orders.count()

    # Returns total amount spent, excluding cancelled orders.
    def get_total_spent(self, obj):
        return sum(
            order.total_amount
            for order in obj.orders.exclude(status="cancelled")
        )