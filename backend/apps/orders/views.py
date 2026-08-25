import stripe
from django.conf import settings
import random
import string

stripe.api_key = settings.STRIPE_SECRET_KEY

from django.db.models import Q
from django.db import transaction
from django.utils import timezone

from rest_framework import status, permissions, generics
from rest_framework.views import APIView
from rest_framework.response import Response

from core.pagination import StandardResultsPagination
from apps.notifications.utils import create_notification

from .models import Customer, Order, OrderItem, Payment
from .serializers import (
    OrderListSerializer,
    AdminOrderListSerializer,
    OrderDetailSerializer,
    CheckoutSerializer,
    CheckoutPrefillSerializer,
    SaveAddressSerializer,
    AdminOrderStatusSerializer,
)

from apps.cart.models import Cart
from apps.products.models import Product, StockMovement
from apps.stores.models import Store
from apps.users.permissions import IsAdmin


def generate_order_number(): # Generates a unique order number for every new order.
    year = timezone.now().year
    last_order = (
        Order.objects.filter(order_number__startswith=f"ORD-{year}-")
        .order_by("-id")
        .first()
    )

    if last_order:
        last_seq = int(last_order.order_number.split("-")[-1])
        new_seq = last_seq + 1
    else:
        new_seq = 1

    return f"ORD-{year}-{new_seq:05d}"

# Finds an existing customer profile or creates one for the current user.
def get_or_create_customer(user, store_id=1):
    customer, _ = Customer.objects.get_or_create(
        user=user,
        store_id=store_id,
        defaults={
            "name": user.name,
            "phone": user.phone or "",
            "email": user.email,
        },
    )
    return customer

# Restores stock for all products when an order is cancelled.
def restore_stock_for_order(order, user=None):
    """
    Shared helper — restores stock for every item in a cancelled order,
    using the same select_for_update() + StockMovement audit pattern as
    checkout and the manual adjust endpoint (stock race-condition fix,
    point 6: cancellations must use the same atomic approach and be
    logged in the same audit trail).

    'user' is the admin who triggered the status change, if any — None
    means the cancellation was triggered by the customer themselves via
    OrderCancelView, which is still a real actor (request.user), so
    callers should pass request.user; this stays None only if truly
    system-triggered elsewhere in the future.
    """
    items = list(order.items.select_related("product").all())
    product_ids = [item.product_id for item in items if item.product_id]

    if not product_ids:
        return

    locked_products = {
        p.id: p
        for p in Product.objects.select_for_update().filter(id__in=product_ids)
    }

    for item in items:
        product = locked_products.get(item.product_id)
        if not product:
            continue

        old_stock = product.stock
        new_stock = old_stock + item.quantity

        product.stock = new_stock
        product.save(update_fields=["stock"])

        StockMovement.objects.create(
            product=product,
            changed_by=user,
            old_stock=old_stock,
            new_stock=new_stock,
            delta=item.quantity,
            reason="order_cancelled",
            note=f"Order {order.order_number} cancelled",
        )

# Handles checkout by creating an order, deducting stock and creating payment.
class CheckoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

# Validates cart, creates order, deducts stock, creates payment and clears cart.
    def post(self, request):
        serializer = CheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        cart = Cart.objects.filter(user=request.user).first()

        if not cart or not cart.items.exists():
            return Response(
                {"error": "Your cart is empty."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # FIX (F8/B18/B19): resolve the customer profile first so we can
        # fall back to their previously saved address/city/postal_code/phone
        # for any field the request didn't send. shipping_address and city
        # are the only two that actually block checkout if still missing
        # after the fallback — postal_code and phone stay optional (B18).
        customer = get_or_create_customer(
            request.user,
            store_id=cart.store_id,
        )

        shipping_address = data.get("shipping_address") or (customer.address or "")
        city = data.get("city") or (customer.city or "")
        postal_code = data.get("postal_code") or (customer.postal_code or "")
        contact_phone = data.get("phone") or (customer.phone or "")

        missing = []
        if not shipping_address.strip():
            missing.append("shipping_address")
        if not city.strip():
            missing.append("city")

        if missing:
            return Response(
                {
                    "error": (
                        "Please provide your shipping details before "
                        f"checking out. Missing: {', '.join(missing)}."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():

            cart_items = list(
                cart.items.select_related("product").all()
            )

            product_ids = [item.product_id for item in cart_items]

            # FIX (stock race-condition): lock the actual Product rows,
            # not just the cart items. Locking CartItem rows alone did
            # not protect against a concurrent checkout or manual stock
            # adjustment touching the same Product at the same time.
            locked_products = {
                p.id: p
                for p in Product.objects.select_for_update().filter(id__in=product_ids)
            }

            out_of_stock = []

            for item in cart_items:
                product = locked_products.get(item.product_id)

                if not product or product.stock < item.quantity:
                    out_of_stock.append(item.product.name if item.product else "Unknown product")

            if out_of_stock:
                return Response(
                    {
                        "error": (
                            "These items are no longer available "
                            f"in the requested quantity: {', '.join(out_of_stock)}"
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            subtotal = sum(
                item.product.price * item.quantity
                for item in cart_items
            )

            discount_amount = 0

            if cart.coupon:
                if cart.coupon.type == "percent":
                    discount_amount = (
                        subtotal * cart.coupon.value
                    ) / 100
                else:
                    discount_amount = cart.coupon.value

                discount_amount = min(
                    discount_amount,
                    subtotal,
                )

            total_amount = subtotal - discount_amount

            order = Order.objects.create(
                store_id=cart.store_id,
                customer=customer,
                order_number=generate_order_number(),
                total_amount=total_amount,
                discount_amount=discount_amount,
                status="pending_payment",
                shipping_address=shipping_address,
                city=city,
                postal_code=postal_code,
                contact_phone=contact_phone,
                notes=data.get("notes", ""),
            )

            # FIX (B22): "Save Address" via checkout — write the resolved
            # values back onto the customer profile so next time everything
            # is prefilled (F8) and this bug can't recur.
            if data.get("save_address"):
                customer.address = shipping_address
                customer.city = city
                customer.postal_code = postal_code
                if contact_phone:
                    customer.phone = contact_phone
                customer.save(
                    update_fields=["address", "city", "postal_code", "phone", "updated_at"]
                )

            for item in cart_items:
                OrderItem.objects.create(
                    order=order,
                    product=item.product,
                    product_name=item.product.name,
                    price=item.product.price,
                    quantity=item.quantity,
                    total_price=item.product.price * item.quantity,
                )

                # FIX (stock race-condition): decrement via the row we
                # already locked above, and log the movement in the same
                # audit table used by the manual adjust endpoint.
                product = locked_products[item.product_id]
                old_stock = product.stock
                new_stock = old_stock - item.quantity

                product.stock = new_stock
                product.save(update_fields=["stock"])

                StockMovement.objects.create(
                    product=product,
                    changed_by=None,
                    old_stock=old_stock,
                    new_stock=new_stock,
                    delta=-item.quantity,
                    reason="order_placed",
                    note=f"Order {order.order_number} checkout",
                )

            Payment.objects.create(
                order=order,
                status="pending",
                amount=total_amount,
            )

            cart.items.all().delete()
            cart.coupon = None
            cart.save()

        create_notification(
            user=request.user,
            title="Order Created",
            message=f"Your order #{order.order_number} has been created and is awaiting payment.",
            notification_type="order",
        )

        return Response(
            OrderDetailSerializer(order).data,
            status=status.HTTP_201_CREATED,
        )

# NEW (F8): lets the frontend prefill the checkout form with whatever the
# customer already has saved, instead of asking them to retype everything.
class CheckoutPrefillView(APIView):
    """GET /api/v1/orders/checkout/prefill/"""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        customer = Customer.objects.filter(user=request.user).first()

        data = {
            "shipping_address": customer.address if customer else "",
            "city": customer.city if customer else "",
            "postal_code": customer.postal_code if customer else "",
            "phone": (customer.phone if customer else "") or request.user.phone or "",
        }
        return Response(CheckoutPrefillSerializer(data).data)


# NEW (B22): "Save Address" as its own action, independent of checkout —
# fixes it being non-functional by giving it a real endpoint to call.
class SaveAddressView(APIView):
    """PUT /api/v1/orders/save-address/"""
    permission_classes = [permissions.IsAuthenticated]

    def put(self, request):
        serializer = SaveAddressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        store = Store.objects.first()
        customer = get_or_create_customer(request.user, store_id=store.id if store else 1)

        customer.address = data["shipping_address"]
        customer.city = data["city"]
        customer.postal_code = data.get("postal_code", customer.postal_code)
        if data.get("phone"):
            customer.phone = data["phone"]
        customer.save(
            update_fields=["address", "city", "postal_code", "phone", "updated_at"]
        )

        return Response(
            {
                "message": "Address saved.",
                "shipping_address": customer.address,
                "city": customer.city,
                "postal_code": customer.postal_code,
                "phone": customer.phone,
            }
        )


# Returns all orders belonging to the logged-in customer.
class OrderListView(generics.ListAPIView):
    serializer_class = OrderListSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsPagination

    def get_queryset(self): # Fetches customer order history.
        return (
            Order.objects.filter(
                customer__user=self.request.user
            )
            .order_by("-created_at")
        )

# Returns complete details of a single order.
class OrderDetailView(generics.RetrieveAPIView):
    serializer_class = OrderDetailSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "order_number"

# Restricts customers to their own orders while allowing admins to view all.
    queryset = (
        Order.objects
        .select_related("customer", "store", "payment")
        .prefetch_related("items")
    )

    def get_queryset(self):
        if self.request.user.is_staff:
            return self.queryset

        return self.queryset.filter(
            customer__user=self.request.user
        )
        
        
# Allows a customer to cancel an order.
class OrderCancelView(APIView):
    """PUT /api/v1/orders/{order_number}/cancel/"""
    permission_classes = [permissions.IsAuthenticated]

# Cancels order, restores stock and updates payment.
    def put(self, request, order_number):
        try:
            order = Order.objects.get(
                order_number=order_number,
                customer__user=request.user,
            )
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if order.status == "delivered":
            return Response(
                {"error": "Delivered orders cannot be cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if order.status == "cancelled":
            return Response(
                {"error": "Order is already cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # FIX (stock race-condition): stock restoration now goes
            # through the shared, locked, audited helper instead of a
            # plain read-modify-save loop.
            restore_stock_for_order(order, user=request.user)

            order.status = "cancelled"
            order.save()

            if hasattr(order, "payment"):
                order.payment.status = "refunded"
                order.payment.save()

        return Response(OrderDetailSerializer(order).data)

# Returns tracking information for an order.
class OrderTrackView(APIView):
    """GET /api/v1/orders/{order_number}/track/"""
    permission_classes = [permissions.IsAuthenticated]

# Fetches current order status and tracking number.
    def get(self, request, order_number):
        try:
            if request.user.is_staff:
                # Admin can view any order
                order = Order.objects.get(order_number=order_number)
            else:
                # Customer can only view their own order
                order = Order.objects.get(
                    order_number=order_number,
                    customer__user=request.user,
                )

        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "order_number": order.order_number,
                "status": order.status,
                "tracking_number": order.tracking_number,
                "updated_at": order.updated_at,
            },
            status=status.HTTP_200_OK,
        )

# ============================================================
# ADMIN VIEWS
# ============================================================

# Returns all orders for administrators.
class AdminOrderListView(generics.ListAPIView):
    """GET /api/v1/admin/orders/"""
    serializer_class = AdminOrderListSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    pagination_class = StandardResultsPagination

# Retrieves every order in the system.
    def get_queryset(self):
        return Order.objects.all().order_by("-created_at")

# Allows admins to update order status.
class AdminOrderStatusUpdateView(APIView):
    """PUT /api/v1/admin/orders/{order_number}/status/"""
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

# Updates order status, restores stock if cancelled and notifies customer.
    def put(self, request, order_number):
        try:
            order = Order.objects.get(order_number=order_number)
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = AdminOrderStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_status = serializer.validated_data["status"]

        if order.status == "delivered" and new_status == "cancelled":
            return Response(
                {"error": "Delivered orders cannot be cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if new_status == "cancelled" and order.status != "cancelled":
            with transaction.atomic():
                # FIX (stock race-condition): same shared, locked,
                # audited helper as the customer-facing cancel view.
                restore_stock_for_order(order, user=request.user)

                if hasattr(order, "payment"):
                    order.payment.status = "refunded"
                    order.payment.save()

        order.status = new_status

        if (
            "tracking_number" in serializer.validated_data
            and serializer.validated_data["tracking_number"]
        ):
            order.tracking_number = serializer.validated_data[
                "tracking_number"
            ]

        order.save()

        status_titles = {
            "pending_payment": "Awaiting Payment",
            "confirmed": "Order Confirmed",
            "shipped": "Order Shipped",
            "delivered": "Order Delivered",
            "cancelled": "Order Cancelled",
        }

        status_messages = {
            "pending_payment": f"Your order #{order.order_number} is awaiting payment.",
            "confirmed": f"Your order #{order.order_number} has been confirmed.",
            "shipped": f"Your order #{order.order_number} is on its way.",
            "delivered": f"Your order #{order.order_number} has been delivered.",
            "cancelled": f"Your order #{order.order_number} has been cancelled.",
        }

        create_notification(
            user=order.customer.user,
            store=order.store,
            title=status_titles.get(new_status, "Order Update"),
            message=status_messages.get(
                new_status,
                f"Your order #{order.order_number} has been updated.",
            ),
            notification_type="order",
        )

        return Response(OrderDetailSerializer(order).data)
    
# Returns filtered order list for administrators.
class AdminOrderFilterView(generics.ListAPIView):
    """
    GET /api/v1/admin/orders/filter/

    Query Params:
    - status
    - start_date
    - end_date
    - search
    - customer_id
    - page
    """

    serializer_class = AdminOrderListSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    pagination_class = StandardResultsPagination

# Filters orders using status, customer, dates and search keywords.
    def get_queryset(self):
        qs = (
            Order.objects
            .select_related("customer")
            .all()
            .order_by("-created_at")
        )

        params = self.request.query_params

        # Status filter
        status = params.get("status")
        if status:
            qs = qs.filter(status=status)

        # Customer filter (NEW)
        customer_id = params.get("customer_id")
        if customer_id:
            qs = qs.filter(customer_id=customer_id)

        # Date filters
        start_date = params.get("start_date")
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)

        end_date = params.get("end_date")
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)

        # Search
        search = params.get("search")
        if search:
            qs = qs.filter(
                Q(order_number__icontains=search) |
                Q(customer__name__icontains=search)
            )

        return qs

# Returns complete details of any order for administrators.
class AdminOrderDetailView(generics.RetrieveAPIView):
    serializer_class = OrderDetailSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = "order_number"

    queryset = (
        Order.objects
        .select_related("customer", "store", "payment")
        .prefetch_related("items")
    )