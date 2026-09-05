#PATH: apps/orders/admin.py

from django.contrib import admin

from .models import Customer, Order, OrderItem, Payment
from apps.returns.models import Return, Complaint


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "phone",
        "email",
        "store",
        "created_at",
    )
    search_fields = (
        "name",
        "phone",
        "email",
    )
    list_filter = (
        "store",
        "created_at",
    )


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order_number",
        "customer",
        "store",
        "total_amount",
        "status",
        "created_at",
    )

    list_filter = (
        "status",
        "store",
        "created_at",
    )

    search_fields = (
        "order_number",
        "customer__name",
        "customer__phone",
    )


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "product_name",
        "quantity",
        "price",
        "total_price",
    )

    search_fields = (
        "order__order_number",
        "product_name",
    )


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "stripe_payment_intent_id",
        "status",
        "amount",
        "paid_at",
        "created_at",
    )

    list_filter = (
        "status",
        "created_at",
    )

    search_fields = (
        "order__order_number",
        "stripe_payment_intent_id",
    )


@admin.register(Return)
class ReturnAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "customer",
        "status",
        "created_at",
    )

    list_filter = (
        "status",
        "created_at",
    )

    search_fields = (
        "order__order_number",
        "customer__name",
    )


@admin.register(Complaint)
class ComplaintAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "customer",
        "type",
        "status",
        "priority",
        "created_at",
    )

    list_filter = (
        "type",
        "priority",
        "status",
        "created_at",
    )

    search_fields = (
        "order__order_number",
        "customer__name",
    )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "order":
            obj_id = request.resolver_match.kwargs.get("object_id")

            if obj_id:
                complaint = Complaint.objects.filter(pk=obj_id).first()
                if complaint:
                    kwargs["queryset"] = Order.objects.filter(
                        customer=complaint.customer
                    )
            else:
                # While creating a new complaint from admin,
                # don't allow selecting orders until a customer is chosen.
                kwargs["queryset"] = Order.objects.none()

        return super().formfield_for_foreignkey(
            db_field,
            request,
            **kwargs,
        )