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

from .models import Customer, Order, OrderItem, Payment, Address
from .serializers import (
    OrderListSerializer,
    AdminOrderListSerializer,
    OrderDetailSerializer,
    CheckoutSerializer,
    CustomerOrderCancelSerializer,
    AdminOrderStatusSerializer,
)

from apps.cart.models import Cart
from apps.products.models import Product, StockMovement
# FIX (B43): IsCustomer ab import ho rahi hai taake customer-only
# endpoints par admin ka login access na kar sake (pehle sirf IsAdmin
# import thi, IsCustomer kahin bhi use nahi ho rahi thi).
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


# ============================================================
# NEW: Reserved Stock Transitions as per PDF Part 2 Item 5
# ============================================================

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

            # Validate that we're not reserving more than available
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
                old_stock=product.total_stock,  # total_stock unchanged
                new_stock=product.total_stock,
                delta=0,
                reason="order_placed",
                note=f"Order {order.order_number} - reserved {item.quantity} units",
            )


def confirm_stock_for_order(order):
    """
    Transition 2: Payment confirmed (Stripe webhook OR QR admin approve)
    total_stock -= qty AND reserved_stock -= qty, same operation, same moment
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

            # Decrease total_stock
            old_total = product.total_stock
            new_total = max(old_total - item.quantity, 0)

            # Decrease reserved_stock
            old_reserved = product.reserved_stock
            new_reserved = max(old_reserved - item.quantity, 0)

            product.total_stock = new_total
            product.reserved_stock = new_reserved
            product.save(update_fields=["total_stock", "reserved_stock"])

            shortfall = item.quantity - (old_total - new_total)
            note = f"Order {order.order_number} payment confirmed"
            if shortfall > 0:
                note += f" (WARNING: {shortfall} units short of stock — oversold, admin follow-up needed)"

            StockMovement.objects.create(
                product=product,
                changed_by=None,
                old_stock=old_total,
                new_stock=new_total,
                delta=new_total - old_total,
                reason="order_confirmed",
                note=note,
            )

    # BUG FIX (cross-check, Sep 2026): every caller of this function
    # (CreatePaymentIntentView free-order path, StripeWebhookView,
    # AdminQRPaymentApproveView) calls order.save() for the status change
    # BEFORE calling confirm_stock_for_order(order) — so setting
    # order.stock_deducted = True here without its own order.save() never
    # actually persisted to the DB. stock_deducted stayed False forever
    # in the database, which broke two things: (1) the idempotency guard
    # at the top of this function and reserve_stock_for_order (a retried
    # Stripe webhook or duplicate approve call could double-deduct
    # total_stock), and (2) release_reserved_stock_for_order's branch
    # selection — a later cancellation of an already-confirmed order
    # would incorrectly take the "not stock_deducted" branch (only
    # decrementing reserved_stock, already 0) instead of restoring
    # total_stock, permanently losing those units. Saving explicitly here
    # fixes it for all three call sites at once.
    order.stock_deducted = True
    order.save(update_fields=["stock_deducted"])

def release_reserved_stock_for_order(order):
    """
    Transition 3: Order cancelled (customer API 58, admin API 63) OR payment timeout
    reserved_stock -= qty, total_stock unchanged
    """
    if not order.stock_deducted:
        # If stock was never deducted, just release reserved
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

    # If stock was deducted (paid order), we need to restore total_stock
    # AND release reserved_stock (but reserved_stock should already be 0 for paid orders)
    # This handles the case where a paid order is cancelled and refunded
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

            # Restore total_stock
            old_total = product.total_stock
            new_total = old_total + item.quantity

            # Release reserved_stock (should be 0, but just in case)
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


def restore_stock_for_order(order, user=None):
    """
    DEPRECATED: Use release_reserved_stock_for_order instead
    Kept for backward compatibility
    """
    release_reserved_stock_for_order(order)


def deduct_stock_for_order(order, user=None):
    """
    DEPRECATED: Use confirm_stock_for_order instead
    Kept for backward compatibility
    """
    confirm_stock_for_order(order)


# ============================================================
# END OF NEW RESERVED STOCK FUNCTIONS
# ============================================================


# NEW (B27): when an admin cancels an order, suggest in-stock alternatives
# for any item that was out of stock — cheap "same category, currently
# available" lookup, top 3 per item.
def suggest_alternatives_for_order(order):
    suggestions = {}

    for item in order.items.select_related("product", "product__category").all():
        product = item.product
        if not product or product.total_stock > 0:
            continue

        alternatives_qs = Product.objects.filter(
            is_active=True,
            is_delete=False,
            total_stock__gt=0,
        ).exclude(id=product.id)

        if product.category_id:
            alternatives_qs = alternatives_qs.filter(category_id=product.category_id)

        alternatives = list(
            alternatives_qs.values("id", "name", "price", "total_stock")[:3]
        )

        if alternatives:
            suggestions[item.product_name] = alternatives

    return suggestions


# Handles checkout by creating an order, validating stock and creating payment.
class CheckoutView(APIView):
    # FIX (B43): admin login ab checkout nahi kar sakta — sirf customer
    # role allowed hai. Pehle IsAuthenticated hi kaafi tha, is liye admin
    # token bhi is endpoint ko hit kar sakta tha.
    permission_classes = [permissions.IsAuthenticated, IsCustomer]

# Validates cart, creates order, and creates a pending payment. Stock is
# intentionally NOT deducted here anymore (see FIX B59 below).
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

        # FIX (B18/B19): resolve the customer profile first.
        customer = get_or_create_customer(
            request.user,
            store_id=cart.store_id,
        )

        # NEW (Backend Change Request v2, Part 1 — item 6): address
        # resolution order is address_id -> manual fields on this request
        # -> the customer's default Address Book entry. shipping_address
        # and city are the only two that actually block checkout if still
        # missing after this — postal_code and phone stay optional (B18).
        address_id = data.get("address_id")
        selected_address = None

        if address_id:
            selected_address = Address.objects.filter(
                id=address_id, customer=customer
            ).first()
            if not selected_address:
                return Response(
                    {"error": "Address not found."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if selected_address:
            shipping_address = selected_address.shipping_address
            city = selected_address.city
            postal_code = selected_address.postal_code or ""
            contact_phone = selected_address.phone or ""
        else:
            shipping_address = data.get("shipping_address") or ""
            city = data.get("city") or ""
            postal_code = data.get("postal_code") or ""
            contact_phone = data.get("phone") or ""

            if not shipping_address.strip() or not city.strip():
                # Nothing typed manually either — fall back to whichever
                # saved address is currently the default.
                default_address = Address.objects.filter(
                    customer=customer, is_default=True
                ).first()
                if default_address:
                    shipping_address = default_address.shipping_address
                    city = default_address.city
                    postal_code = default_address.postal_code or ""
                    contact_phone = default_address.phone or ""

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

            # Lock the actual Product rows while we check availability
            locked_products = {
                p.id: p
                for p in Product.objects.select_for_update().filter(id__in=product_ids)
            }

            out_of_stock = []

            for item in cart_items:
                product = locked_products.get(item.product_id)

                # Check available_stock (total_stock - reserved_stock)
                available = product.total_stock - product.reserved_stock if product else 0

                if not product or available < item.quantity:
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
            # NEW: Reserve stock (Transition 1)
            # ============================================================
            reserve_stock_for_order(order)

            # FIX (Cross-check, Sep 2026): payment_method was being
            # validated by CheckoutSerializer but never actually passed
            # into Payment.objects.create() below — every order silently
            # defaulted to the model's payment_method="stripe", even when
            # the customer picked "qr" at checkout. That broke the whole
            # QR flow at its root: QRProofUploadView/AdminQRPayment* views
            # all check payment.payment_method == "qr" and would reject a
            # QR order that the DB actually still thought was "stripe".
            payment_method = data["payment_method"]

            Payment.objects.create(
                order=order,
                status="pending",
                amount=total_amount,
                payment_method=payment_method,
            )

            cart.items.all().delete()
            cart.coupon = None
            cart.save()

        create_notification(
            user=request.user,
            title="Order Created",
            message=f"Your order #{order.order_number} has been created and is awaiting payment.",
            notification_type="order",
            reference_type="order",
            reference_id=order.order_number,
        )

        # FIX (F14): actual email confirmation, not just an in-app
        # notification. Failure here must never break checkout for the
        # customer — send_order_confirmation_email swallows/logs its own
        # exceptions.
        send_order_confirmation_email(order)

        response_data = OrderDetailSerializer(order).data

        # NEW (Cross-check, Sep 2026 — Part 3): these two fields were
        # missing from the checkout response entirely for QR orders.
        # qr_image_url is static/config-driven (one image per gateway,
        # NOT generated per transaction, per spec) — set
        # QR_PAYMENT_IMAGE_URL in settings.py or the environment; falls
        # back to an empty string (never crashes checkout) if unset, but
        # the frontend won't have anything to show, so this needs a real
        # value configured before QR goes live.
        if payment_method == "qr":
            response_data["qr_image_url"] = getattr(
                settings, "QR_PAYMENT_IMAGE_URL", ""
            )
            # payment_reference = the order_number, to be written in the
            # bank transfer note (per spec, Part 3) — same value the
            # customer needs to reference when they upload proof.
            response_data["payment_reference"] = order.order_number

        return Response(
            response_data,
            status=status.HTTP_201_CREATED,
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
    # FIX (B43): customer-only — this returns the logged-in user's own
    # order history, so an admin account should never be able to call it.
    permission_classes = [permissions.IsAuthenticated, IsCustomer]
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
    # FIX (B43): customer-only.
    permission_classes = [permissions.IsAuthenticated, IsCustomer]

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

        # NEW (Backend Change Request v2, Part 2 — Item 1 / Issue 3):
        # reason is optional — request.data being empty (no body sent at
        # all) still validates fine here, so this is purely additive.
        cancel_serializer = CustomerOrderCancelSerializer(data=request.data)
        cancel_serializer.is_valid(raise_exception=True)
        reason = cancel_serializer.validated_data.get("reason", "").strip()

        with transaction.atomic():
            # ============================================================
            # NEW: Release reserved stock (Transition 3)
            # ============================================================
            release_reserved_stock_for_order(order)

            order.status = "cancelled"
            if reason:
                order.cancellation_reason = reason
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
            title="Order Cancelled",
            message=(
                f"Your order #{order.order_number} has been cancelled. "
                f"Reason: {reason}"
                if reason
                else f"Your order #{order.order_number} has been cancelled."
            ),
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

            # NEW (Backend Change Request v2, Part 2 — Item 2 / Issue 5):
            # for a QR-paid order being cancelled, refund_transaction_reference
            # is mandatory — reject with 400 before touching anything if
            # it's missing. Stripe orders are untouched (refund stays
            # automatic, this field is never required/sent for them).
            order_payment = getattr(order, "payment", None)
            is_qr_order = bool(order_payment and order_payment.payment_method == "qr")
            refund_transaction_reference = serializer.validated_data.get(
                "refund_transaction_reference", ""
            ).strip()

            if is_qr_order and not refund_transaction_reference:
                return Response(
                    {
                        "refund_transaction_reference": (
                            "This field is required when cancelling a QR-paid order."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # FIX (B27): surface in-stock alternatives for any item that
            # was out of stock, so the admin can pass them on to the
            # customer instead of just saying "sorry, cancelled".
            suggested_alternatives = suggest_alternatives_for_order(order)

            with transaction.atomic():
                # ============================================================
                # NEW: Release reserved stock (Transition 3)
                # ============================================================
                release_reserved_stock_for_order(order)

                if hasattr(order, "payment"):
                    was_paid_before_cancel = order.payment.status == "paid"
                    order.payment.status = "refunded"
                    order.payment.refunded_at = timezone.now()
                    # NEW (Item 2): QR orders get refund_method="manual"
                    # (the only valid value per spec for this case) plus
                    # the admin-supplied reference, stored on the payment
                    # so every future response for this order includes
                    # them. Stripe orders are left alone — refund_method
                    # stays null, refund is automatic, exactly as before.
                    if is_qr_order:
                        order.payment.refund_method = "manual"
                        order.payment.refund_transaction_reference = refund_transaction_reference
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
            "out_for_delivery": "Out for Delivery",
            "delivered": "Order Delivered",
            "cancelled": "Order Cancelled",
        }

        status_messages = {
            "pending_payment": f"Your order #{order.order_number} is awaiting payment.",
            "confirmed": f"Your order #{order.order_number} has been confirmed.",
            "shipped": f"Your order #{order.order_number} has been shipped.",
            "out_for_delivery": f"Your order #{order.order_number} is out for delivery.",
            "delivered": f"Your order #{order.order_number} has been delivered.",
            "cancelled": (
                f"Your order #{order.order_number} has been cancelled. "
                f"Reason: {order.cancellation_reason}"
                if order.cancellation_reason
                else f"Your order #{order.order_number} has been cancelled."
            ),
        }

        # FIX (B28): this notification already existed and already fires
        # correctly on every admin status change (including cancellation)
        # — kept as-is, just extended with the new statuses above so the
        # customer's status always matches what the admin set.
        create_notification(
            user=order.customer.user,
            store=order.store,
            title=status_titles.get(new_status, "Order Update"),
            message=status_messages.get(
                new_status,
                f"Your order #{order.order_number} has been updated.",
            ),
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
            reference_type="order",
            reference_id=order.order_number,
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
    - ordering (created_at, total_amount — see D1 fix below)
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
        # FIX (A5 - CRITICAL): search ab phone number pe bhi match karta h.
        # Do jagah check ki ja rahi hain: Order.contact_phone (jo checkout
        # k waqt diya gaya tha, guest checkout mei customer.phone se
        # different ho sakta h) aur Customer.phone (customer ka saved
        # number). Pehle sirf order_number aur customer name match hota
        # tha, is liye admin phone number se order dhoond nahi pate the.
        search = params.get("search")
        if search:
            qs = qs.filter(
                Q(order_number__icontains=search) |
                Q(customer__name__icontains=search) |
                Q(contact_phone__icontains=search) |
                Q(customer__phone__icontains=search)
            )

        # FIX (D1): 'ordering' — created_at / total_amount, both real
        # DB columns on Order, so no annotation needed (unlike the
        # customers endpoint). Whitelisted the same way as Products'
        # search endpoint — unknown values are silently ignored rather
        # than raising a DB error.
        allowed_ordering_fields = {
            'created_at', '-created_at',
            'total_amount', '-total_amount',
        }
        ordering = params.get('ordering')
        if ordering in allowed_ordering_fields:
            qs = qs.order_by(ordering)

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