# PATH: apps/orders/views.py

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
from apps.notifications.utils import (
    create_notification,
    send_order_confirmation_email,
    send_refund_confirmation_email,
)

from .models import Address, Customer, Order, OrderItem, Payment
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
from apps.users.permissions import IsAdmin, IsCustomer

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

def reserve_stock_for_order(order):
    """
    Transition 1: Checkout (API 55), order created as pending_payment
    reserved_stock += qty, total_stock unchanged
    """
    if order.stock_deducted:
        return

    items = list(order.items.select_related("product").all())
    product_ids = [item.product_id for item in items if item.product_id]

    if product_ids:
        locked_products = {
            p.id: p
            for p in Product.objects.select_for_update().filter(id__in=product_ids)
        }

        for item in items:
            product = locked_products.get(item.product_id)
            if not product:
                continue

            old_reserved = product.reserved_stock
            new_reserved = old_reserved + item.quantity

            if new_reserved > product.total_stock:
                raise Exception(
                    f"Cannot reserve {item.quantity} units for product {product.name}. "
                    f"Available stock: {product.total_stock - old_reserved}"
                )

            product.reserved_stock = new_reserved
            product.save(update_fields=["reserved_stock"])

            StockMovement.objects.create(
                product=product,
                changed_by=None,
                old_stock=product.total_stock,
                new_stock=product.total_stock,
                delta=0,
                reason="order_placed",
                note=f"Order {order.order_number} - reserved {item.quantity} units",
            )


def release_reserved_stock_for_order(order):
    """
    Transition 3: Order cancelled (customer API 58, admin API 63)
    OR payment timeout.

    If payment was never confirmed:
        reserved_stock -= qty
        total_stock unchanged

    If payment was confirmed:
        total_stock += qty
        reserved_stock -= qty
    """
    if not order.stock_deducted:
        items = list(order.items.select_related("product").all())
        product_ids = [item.product_id for item in items if item.product_id]

        if product_ids:
            locked_products = {
                p.id: p
                for p in Product.objects.select_for_update().filter(id__in=product_ids)
            }

            for item in items:
                product = locked_products.get(item.product_id)
                if not product:
                    continue

                old_reserved = product.reserved_stock
                new_reserved = max(old_reserved - item.quantity, 0)

                product.reserved_stock = new_reserved
                product.save(update_fields=["reserved_stock"])

                StockMovement.objects.create(
                    product=product,
                    changed_by=None,
                    old_stock=product.total_stock,
                    new_stock=product.total_stock,
                    delta=0,
                    reason="order_cancelled",
                    note=f"Order {order.order_number} - released {item.quantity} units",
                )

        return

    items = list(order.items.select_related("product").all())
    product_ids = [item.product_id for item in items if item.product_id]

    if product_ids:
        locked_products = {
            p.id: p
            for p in Product.objects.select_for_update().filter(id__in=product_ids)
        }

        for item in items:
            product = locked_products.get(item.product_id)
            if not product:
                continue

            old_total = product.total_stock
            new_total = old_total + item.quantity

            old_reserved = product.reserved_stock
            new_reserved = max(old_reserved - item.quantity, 0)

            product.total_stock = new_total
            product.reserved_stock = new_reserved
            product.save(update_fields=["total_stock", "reserved_stock"])

            StockMovement.objects.create(
                product=product,
                changed_by=None,
                old_stock=old_total,
                new_stock=new_total,
                delta=item.quantity,
                reason="order_cancelled",
                note=f"Order {order.order_number} cancelled - restored {item.quantity} units",
            )

    order.stock_deducted = False

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

    FIX (B59): stock is only ever deducted once payment is confirmed (see
    deduct_stock_for_order below), so restoring stock only makes sense —
    and must only happen — if this specific order actually had stock
    deducted in the first place. Without this guard, cancelling an unpaid
    order (which never touched stock) would incorrectly ADD stock back
    that was never removed. order.stock_deducted is the single source of
    truth for this and gets flipped back to False here; callers still do
    their own order.save() right after, which persists it.
    """
    if not order.stock_deducted:
        return

    items = list(order.items.select_related("product").all())
    product_ids = [item.product_id for item in items if item.product_id]

    if product_ids:
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

    order.stock_deducted = False


# NEW (B59): stock now only leaves inventory once payment is actually
# confirmed — not at checkout/pending_payment time. Called from
# payments/views.py at the two places an order genuinely becomes paid:
# CreatePaymentIntentView's free-order (Rs. 0) branch, and
# StripeWebhookView's payment_intent.succeeded handler.
def deduct_stock_for_order(order, user=None):
    """
    Transition 2: Payment confirmed.

    reserved_stock -= quantity
    total_stock -= quantity

    This is idempotent through order.stock_deducted, so a duplicate
    Stripe webhook cannot deduct stock twice.
    """
    if order.stock_deducted:
        return

    items = list(order.items.select_related("product").all())
    product_ids = [item.product_id for item in items if item.product_id]

    if product_ids:
        locked_products = {
            p.id: p
            for p in Product.objects.select_for_update().filter(
                id__in=product_ids
            )
        }

        for item in items:
            product = locked_products.get(item.product_id)
            if not product:
                continue

            old_total = product.total_stock
            old_reserved = product.reserved_stock

            # The quantity was reserved during checkout.
            # Now payment is confirmed, so it becomes permanently sold.
            new_total = max(old_total - item.quantity, 0)
            new_reserved = max(old_reserved - item.quantity, 0)

            product.total_stock = new_total
            product.reserved_stock = new_reserved

            product.save(
                update_fields=[
                    "total_stock",
                    "reserved_stock",
                ]
            )

            StockMovement.objects.create(
                product=product,
                changed_by=user,
                old_stock=old_total,
                new_stock=new_total,
                delta=new_total - old_total,
                reason="order_confirmed",
                note=(
                    f"Order {order.order_number} payment confirmed - "
                    f"deducted {item.quantity} units"
                ),
            )

    order.stock_deducted = True
    order.save(update_fields=["stock_deducted"])
# Backward-compatible name used by older payment code.
# Stock confirmation now uses the same safe deduction helper.
def confirm_stock_for_order(order, user=None):
    return deduct_stock_for_order(order, user=user)


# NEW (B27): when an admin cancels an order, suggest in-stock alternatives
# for any item that was out of stock — cheap "same category, currently
# available" lookup, top 3 per item.
def suggest_alternatives_for_order(order):
    suggestions = {}

    for item in order.items.select_related("product", "product__category").all():
        product = item.product
        if not product or product.available_stock > 0:
            continue

        alternatives_qs = Product.objects.filter(
    is_delete=False,
    total_stock__gt=0,
).exclude(id=product.id)

        if product.category_id:
            alternatives_qs = alternatives_qs.filter(category_id=product.category_id)

        alternatives = list(
            alternatives_qs.values("id", "name", "price", "stock")[:3]
        )

        if alternatives:
            suggestions[item.product_name] = alternatives

    return suggestions

# Handles checkout by creating an order, validating stock and creating payment.
# Handles checkout by creating an order, validating stock and creating payment.
class CheckoutView(APIView):
    # FIX (B43): only customers can checkout.
    # Admin users must not be allowed to create customer orders.
    permission_classes = [permissions.IsAuthenticated, IsCustomer]

    # Validates cart, creates order, and creates a pending payment.
    # Stock is reserved at checkout but is only deducted from total_stock
    # after payment is actually confirmed.
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

        # Resolve the customer profile first.
        customer = get_or_create_customer(
            request.user,
            store_id=cart.store_id,
        )

        # ============================================================
        # ADDRESS RESOLUTION
        # Priority:
        # 1. address_id from Address Book
        # 2. manually supplied address fields
        # 3. customer's default Address Book address
        # ============================================================

        address_id = data.get("address_id")
        selected_address = None

        if address_id:
            selected_address = Address.objects.filter(
                id=address_id,
                customer=customer,
            ).first()

            if not selected_address:
                return Response(
                    {"error": "Address not found."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if selected_address:
            # Customer selected an existing Address Book entry.
            shipping_address = selected_address.shipping_address
            city = selected_address.city
            postal_code = selected_address.postal_code or ""
            contact_phone = selected_address.phone or ""

        else:
            # No address_id: use manually supplied checkout fields.
            shipping_address = data.get("shipping_address") or ""
            city = data.get("city") or ""
            postal_code = data.get("postal_code") or ""
            contact_phone = data.get("phone") or ""

            # If manual shipping address/city is incomplete,
            # fall back to the customer's default saved address.
            if not shipping_address.strip() or not city.strip():
                default_address = Address.objects.filter(
                    customer=customer,
                    is_default=True,
                ).first()

                if default_address:
                    shipping_address = default_address.shipping_address
                    city = default_address.city
                    postal_code = default_address.postal_code or ""
                    contact_phone = default_address.phone or ""

        # shipping_address and city are required.
        # postal_code and phone remain optional.
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

            product_ids = [
                item.product_id
                for item in cart_items
                if item.product_id
            ]

            # Lock Product rows so concurrent checkouts cannot both
            # read the same stale stock values.
            locked_products = {
                p.id: p
                for p in Product.objects.select_for_update().filter(
                    id__in=product_ids
                )
            }

            out_of_stock = []

            for item in cart_items:
                product = locked_products.get(item.product_id)

                # Available stock = total stock minus already reserved stock.
                available = (
                    product.total_stock - product.reserved_stock
                    if product
                    else 0
                )

                if not product or available < item.quantity:
                    out_of_stock.append(
                        item.product.name
                        if item.product
                        else "Unknown product"
                    )

            if out_of_stock:
                return Response(
                    {
                        "error": (
                            "These items are no longer available "
                            f"in the requested quantity: "
                            f"{', '.join(out_of_stock)}"
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # ============================================================
            # CALCULATE ORDER TOTAL
            # ============================================================

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

            # ============================================================
            # CREATE ORDER
            # ============================================================

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

            # ============================================================
            # CREATE ORDER ITEMS
            # ============================================================

            for item in cart_items:
                OrderItem.objects.create(
                    order=order,
                    product=item.product,
                    product_name=item.product.name,
                    price=item.product.price,
                    quantity=item.quantity,
                    total_price=item.product.price * item.quantity,
                )

            # ============================================================
            # RESERVE STOCK
            #
            # Checkout:
            # reserved_stock += quantity
            # total_stock remains unchanged.
            #
            # Actual total_stock deduction happens only after payment
            # confirmation.
            # ============================================================

            reserve_stock_for_order(order)

            # ============================================================
            # CREATE PAYMENT
            # ============================================================

            payment_method = data["payment_method"]

            Payment.objects.create(
                order=order,
                status="pending",
                amount=total_amount,
                payment_method=payment_method,
            )

            # ============================================================
            # CLEAR CART
            # ============================================================

            cart.items.all().delete()
            cart.coupon = None
            cart.save()

        # ================================================================
        # NOTIFICATION
        # ================================================================

        create_notification(
            user=request.user,
            title="Order placed",
            message=(
                f"Your order {order.order_number} has been placed "
                "and is awaiting payment."
            ),
            notification_type="order",
            reference_type="order",
            reference_id=order.order_number,
        )

        # ================================================================
        # EMAIL
        # ================================================================

        # Email failures must not break checkout.
        send_order_confirmation_email(order)

        # ================================================================
        # RESPONSE
        # ================================================================

        response_data = OrderDetailSerializer(order).data

        # QR payment requires the configured static QR image URL.
        if payment_method == "qr":
            response_data["qr_image_url"] = getattr(
                settings,
                "QR_PAYMENT_IMAGE_URL",
                "",
            )

        return Response(
            response_data,
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
    """GET /api/v1/orders/

    FIX (B32): supports optional start_date / end_date query params (e.g.
    ?start_date=2026-08-01&end_date=2026-08-25) so customers can filter
    their own order history by date range, mirroring what admins already
    had via AdminOrderFilterView.
    """
    serializer_class = OrderListSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsPagination

    def get_queryset(self): # Fetches customer order history.
        qs = (
            Order.objects.filter(
                customer__user=self.request.user
            )
            .order_by("-created_at")
        )

        params = self.request.query_params

        start_date = params.get("start_date")
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)

        end_date = params.get("end_date")
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)

        return qs

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

# Cancels order, restores stock (if any was deducted) and updates payment.
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
            # plain read-modify-save loop. FIX (B59): the helper itself is
            # now a no-op if this order never had stock deducted in the
            # first place (i.e. it was still unpaid), so cancelling an
            # unpaid order no longer incorrectly adds phantom stock back.
            restore_stock_for_order(order, user=request.user)

            order.status = "cancelled"
            order.save()

            # FIX (B29): refunded_at timestamp gives a real, checkable
            # confirmation that the refund was processed, instead of just
            # a silent status flip.
            was_paid = False
            if hasattr(order, "payment"):
                was_paid = order.payment.status == "paid"
                order.payment.status = "refunded"
                order.payment.refunded_at = timezone.now()
                order.payment.save()

        # FIX (B28): customer-initiated cancellation previously created no
        # notification at all, unlike the admin-initiated path — so the
        # customer had nothing confirming the cancellation actually
        # registered on their side.
        create_notification(
    user=request.user,
    title="Order cancelled",
    message=f"Your order {order.order_number} has been cancelled.",
    notification_type="order",
    reference_type="order",
    reference_id=order.order_number,
)

        if was_paid:
            send_refund_confirmation_email(order)

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

        # FIX (B29): admin could previously mark an order "shipped" or
        # "delivered" even though it had never actually been paid for.
        # Block that transition outright — payment must be confirmed
        # ("paid") before an order can move to shipped / out_for_delivery
        # / delivered.
        if new_status in ("shipped", "out_for_delivery", "delivered"):
            payment = getattr(order, "payment", None)
            if not payment or payment.status != "paid":
                return Response(
                    {
                        "error": (
                            "This order's payment has not been confirmed yet — "
                            "it cannot be marked as "
                            f"'{new_status.replace('_', ' ')}' until payment is received."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        suggested_alternatives = {}
        was_paid_before_cancel = False

        if new_status == "cancelled" and order.status != "cancelled":
            # FIX (B27): reason is now mandatory for admin cancellations
            # (enforced in AdminOrderStatusSerializer.validate) and gets
            # stored on the order so there's a permanent record of why.
            order.cancellation_reason = serializer.validated_data.get(
                "cancellation_reason", ""
            ).strip()

            # FIX (B27): surface in-stock alternatives for any item that
            # was out of stock, so the admin can pass them on to the
            # customer instead of just saying "sorry, cancelled".
            suggested_alternatives = suggest_alternatives_for_order(order)

            with transaction.atomic():
                # FIX (stock race-condition + B59): same shared, locked,
                # audited helper as the customer-facing cancel view — and
                # it's a no-op if this order's stock was never deducted
                # (i.e. it was cancelled before payment was ever confirmed).
                restore_stock_for_order(order, user=request.user)

                if hasattr(order, "payment"):
                    was_paid_before_cancel = order.payment.status == "paid"
                    order.payment.status = "refunded"
                    order.payment.refunded_at = timezone.now()
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
    "confirmed": "Order confirmed",
    "shipped": "Order shipped",
    "out_for_delivery": "Out for delivery",
    "delivered": "Order delivered",
    "cancelled": "Order cancelled",
}

        status_messages = {
    "pending_payment": f"Your order {order.order_number} is awaiting payment.",
    "confirmed": f"Order {order.order_number} has been confirmed.",
    "shipped": f"Order {order.order_number} has been shipped.",
    "out_for_delivery": f"Order {order.order_number} is out for delivery.",
    "delivered": f"Order {order.order_number} has been delivered.",
    "cancelled": (
        f"Order {order.order_number} has been cancelled. "
        f"Reason: {order.cancellation_reason}."
        if order.cancellation_reason
        else f"Order {order.order_number} has been cancelled."
    ),
}


        # FIX (B28): this notification already existed and already fires
        # correctly on every admin status change (including cancellation)
        # — kept as-is, just extended with the new statuses above so the
        # customer's status always matches what the admin set.
        create_notification(
            user=order.customer.user,
            store=order.store,
            title=status_titles[new_status],
            message=status_messages[new_status],
            notification_type="order",
            reference_type="order",
            reference_id=order.order_number,
        )

        if new_status == "cancelled" and was_paid_before_cancel:
            send_refund_confirmation_email(order)

        response_data = OrderDetailSerializer(order).data
        if suggested_alternatives:
            response_data["suggested_alternatives"] = suggested_alternatives

        return Response(response_data)


# NEW (B29): "ability to re-pay for a reinstated order" — lets an admin
# undo a cancellation and put the order back into pending_payment so the
# customer can pay for it again via the existing, already-working Pay Now
# flow (CreatePaymentIntentView in payments/views.py already supports any
# order_number whose payment isn't "paid" yet — B31 needed no backend
# change, this is what makes it usable for a previously-cancelled order).
class AdminOrderReinstateView(APIView):
    """PUT /api/v1/admin/orders/{order_number}/reinstate/"""
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def put(self, request, order_number):
        try:
            order = Order.objects.get(order_number=order_number)
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if order.status != "cancelled":
            return Response(
                {"error": "Only a cancelled order can be reinstated."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order.status = "pending_payment"
        order.cancellation_reason = None
        order.save()

        if hasattr(order, "payment"):
            # A fresh PaymentIntent must be created next time the customer
            # pays — clearing the old id prevents CreatePaymentIntentView's
            # "already paid" check from getting confused by stale data, and
            # avoids ever reusing a Stripe intent tied to the old attempt.
            order.payment.status = "pending"
            order.payment.stripe_payment_intent_id = None
            order.payment.paid_at = None
            order.payment.refunded_at = None
            order.payment.save()

        create_notification(
            user=order.customer.user,
            store=order.store,
            title="Order Reinstated",
            message=(
                f"Your order #{order.order_number} has been reinstated. "
                "You can complete payment to proceed with it."
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
