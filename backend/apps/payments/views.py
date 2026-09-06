import stripe
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import os

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import (
    authentication_classes,
    permission_classes,
)

from apps.orders.models import Order, Payment
from apps.orders.views import deduct_stock_for_order, confirm_stock_for_order
from apps.users.permissions import IsAdmin
from apps.stores.models import Store
from apps.notifications.utils import create_notification
from core.pagination import StandardResultsPagination

stripe.api_key = settings.STRIPE_SECRET_KEY


class CreatePaymentIntentView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        order_number = request.data.get("order_number")

        if not order_number:
            return Response(
                {"error": "order_number is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            order = Order.objects.get(
                order_number=order_number,
                customer__user=request.user,
            )
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        payment = getattr(order, "payment", None)
        if payment is None:
            return Response(
                {"error": "This order has no payment record."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # FIX: don't let an already-paid order create a second Stripe
        # PaymentIntent (that's how you end up double-charging a customer).
        # NOTE (B31): this same check is also exactly what makes "Pay Now"
        # work for a pending_payment order — as long as payment.status
        # isn't "paid" yet, this endpoint can be called again for the same
        # order_number to resume payment. No separate endpoint is needed
        # for B31 on the backend; the frontend just needs to call this with
        # the pending order's order_number.
        if payment.status == "paid":
            return Response(
                {"error": "This order has already been paid."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # FIX (B16 — "Stripe form shows all values as zero"): when a coupon
        # discounts an order down to Rs 0 (or total_amount is somehow unset),
        # the old code still called Stripe with amount=0, which is exactly
        # what produced a payment form full of zeros. A free order should
        # never reach Stripe at all — it gets marked paid directly.
        total_amount = order.total_amount or Decimal("0")
        if total_amount <= 0:
            with transaction.atomic():
                payment.status = "paid"
                payment.paid_at = timezone.now()
                payment.save()

                order.status = "confirmed"
                order.save()

                # FIX (B59): this is one of the two real "payment confirmed"
                # moments — stock must be deducted now, not back at checkout.
                confirm_stock_for_order(order)

            return Response(
                {
                    "free_order": True,
                    "message": "Order total is Rs. 0 after discount — no payment needed.",
                    "order_number": order.order_number,
                }
            )

        # ============================================================
        # NEW: If payment method is "qr", don't create Stripe intent
        # ============================================================
        if payment.payment_method == "qr":
            return Response(
                {
                    "error": "QR payments do not require Stripe. Please upload proof via /api/v1/payments/qr/proof/",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # FIX: quantize before converting to paisa so a value like 1299.995
        # rounds instead of getting silently truncated by int().
        amount_in_paisa = int(
            (total_amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )

        intent = stripe.PaymentIntent.create(
            amount=amount_in_paisa,
            currency="pkr",
            metadata={
                "order_id": order.id,
                "order_number": order.order_number,
            },
        )

        payment.stripe_payment_intent_id = intent.id
        payment.save()

        # FIX (B20 — "coupon doesn't carry to payment step"): return the
        # subtotal/discount alongside the final amount so the payment page
        # can display them without the frontend having to re-fetch and
        # recompute the cart (which is where the coupon was already
        # cleared by the time checkout finishes).
        #
        # NEW (Shipping cost fix — Sep 2026): total_amount now includes
        # shipping_cost (subtotal - discount_amount + shipping_cost), so
        # subtotal has to be reconstructed by removing shipping_cost too,
        # not just adding discount_amount back — otherwise subtotal would
        # be overstated by exactly the shipping amount. `amount` (paisa
        # sent to Stripe) is derived straight from total_amount above, so
        # it already includes shipping — this is the actual charge fix.
        subtotal = total_amount + order.discount_amount - order.shipping_cost

        return Response(
            {
                "client_secret": intent.client_secret,
                "publishable_key": settings.STRIPE_PUBLISHABLE_KEY,
                "amount": amount_in_paisa,
                "currency": "pkr",
                "subtotal": str(subtotal),
                "discount_amount": str(order.discount_amount),
                "shipping_cost": str(order.shipping_cost),
                "total_amount": str(total_amount),
                "order_number": order.order_number,
            }
        )


@authentication_classes([])
@permission_classes([])
class StripeWebhookView(APIView):

    def post(self, request):
        payload = request.body
        sig_header = request.META.get("HTTP_STRIPE_SIGNATURE")

        try:
            event = stripe.Webhook.construct_event(
                payload,
                sig_header,
                settings.STRIPE_WEBHOOK_SECRET,
            )
        except ValueError:
            return Response(status=400)
        except stripe.error.SignatureVerificationError:
            return Response(status=400)

        if event["type"] == "payment_intent.succeeded":
            intent = event["data"]["object"]

            order_number = intent["metadata"]["order_number"]            
            try:
                order = Order.objects.get(order_number=order_number)

                with transaction.atomic():
                    payment = order.payment
                    payment.status = "paid"
                    payment.paid_at = timezone.now()
                    payment.save()

                    order.status = "confirmed"
                    order.save()

                    # FIX (B59): the second, and main, "payment confirmed"
                    # moment — real Stripe payments go through here. Stock
                    # is deducted only now, not at checkout time.
                    confirm_stock_for_order(order)

                    # Notification
                    # Notification: Stripe payment confirmed
                    create_notification(
    user=order.customer.user,
    store=order.store,
    title="Payment confirmed",
    message=(
        f"Payment for order {order.order_number} has been confirmed. "
        "Your order is now being processed."
    ),
    notification_type="order",
    reference_type="order",
    reference_id=order.order_number,
)

            except Order.DoesNotExist:
                pass

        elif event["type"] == "payment_intent.payment_failed":
            intent = event["data"]["object"]

            order_number = intent["metadata"].get("order_number")

            try:
                order = Order.objects.get(order_number=order_number)

                payment = order.payment
                # FIX (Cross-check, Sep 2026 — PDF Part 3): spec locks
                # payment.status to exactly five values — pending |
                # under_review | paid | rejected | refunded, "no other
                # values, for ALL orders regardless of method" — "failed"
                # was being written here, which isn't one of them and
                # isn't even a valid choice on the model. A failed Stripe
                # attempt just means the customer needs to retry; the
                # order never left pending_payment, so payment.status
                # resets to "pending" (its own default/starting value)
                # rather than recording an out-of-spec state.
                payment.status = "pending"
                payment.save()

            except Order.DoesNotExist:
                pass

        return Response(status=200)


# ============================================================
# QR CODE PAYMENT FLOW (Part 3)
# ============================================================

class QRProofUploadView(APIView):
    """
    POST /api/v1/payments/qr/proof/
    Request (multipart/form-data):
        - order_number: string (required)
        - screenshot: file (image, required)
        - transaction_id: string (optional)

    Effect: payment.status -> under_review
            order.status stays pending_payment
            No stock change (stays reserved)
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        order_number = request.data.get("order_number")
        screenshot = request.FILES.get("screenshot")
        transaction_id = request.data.get("transaction_id", "")

        if not order_number:
            return Response(
                {"error": "order_number is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not screenshot:
            return Response(
                {"error": "screenshot is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check if image is valid
        if not screenshot.content_type.startswith("image/"):
            return Response(
                {"error": "File must be an image"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            order = Order.objects.get(
                order_number=order_number,
                customer__user=request.user,
            )
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        payment = getattr(order, "payment", None)
        if payment is None:
            return Response(
                {"error": "This order has no payment record."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # QR payment only
        if payment.payment_method != "qr":
            return Response(
                {"error": "This order is not a QR payment order."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check if order is already paid
        if payment.status == "paid":
            return Response(
                {"error": "This order has already been paid."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check if order is cancelled
        if order.status == "cancelled":
            return Response(
                {"error": "This order has been cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ============================================================
        # Duplicate proof detection (Part 3.6)
        # ============================================================
        duplicate_warning = False

        # Hash the screenshot file
        file_hash = hashlib.sha256(screenshot.read()).hexdigest()
        screenshot.seek(0)  # Reset file pointer

        # Check for duplicate image hash
        duplicate_by_hash = Payment.objects.filter(
            qr_image_hash=file_hash,
            order__isnull=False,
        ).exclude(order=order).exists()

        # Check for duplicate transaction_id
        duplicate_by_transaction = False
        if transaction_id:
            duplicate_by_transaction = Payment.objects.filter(
                qr_transaction_id=transaction_id,
                order__isnull=False,
            ).exclude(order=order).exists()

        if duplicate_by_hash or duplicate_by_transaction:
            duplicate_warning = True

        # ============================================================
        # Save screenshot
        # ============================================================
        # Generate unique filename
        ext = os.path.splitext(screenshot.name)[1]
        filename = f"qr_proofs/{order_number}_{timezone.now().strftime('%Y%m%d%H%M%S')}{ext}"
        file_path = default_storage.save(filename, ContentFile(screenshot.read()))
        screenshot_url = default_storage.url(file_path)

        # ============================================================
        # Update payment
        # ============================================================
        payment.status = "under_review"
        payment.qr_screenshot_url = screenshot_url
        payment.qr_transaction_id = transaction_id or None
        payment.qr_submitted_at = timezone.now()
        payment.qr_image_hash = file_hash
        payment.qr_duplicate_warning = duplicate_warning
        payment.qr_reject_reason = None  # Clear previous rejection reason
        payment.save()

        # ============================================================
        # Notification to customer
        # ============================================================
        create_notification(
            user=request.user,
            store=order.store,
            title="QR Payment Proof Submitted",
            message=f"Your payment proof for order #{order_number} has been submitted and is under review.",
            notification_type="system",
            reference_type="order",
            reference_id=order_number,
        )

        # ============================================================
        # Response
        # ============================================================
        return Response({
            "order_number": order_number,
            "payment": {
                "status": "under_review",
                "screenshot_url": screenshot_url,
            },
            "duplicate_warning": duplicate_warning,
        }, status=status.HTTP_200_OK)


class AdminQRPaymentPendingView(APIView):
    """
    GET /api/v1/admin/payments/qr/pending/
    Paginated, standard shape {count, next, previous, results}
    Each result: {order_number, customer, amount, screenshot_url, transaction_id, submitted_at, duplicate_warning}
    """
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get(self, request):
        # Get all QR payments with status "under_review"
        payments = Payment.objects.filter(
            payment_method="qr",
            status="under_review",
        ).select_related(
            "order", "order__customer", "order__customer__user"
        ).order_by("qr_submitted_at")

        # FIX (Cross-check, Sep 2026): this was returning every matching
        # payment in one response with count hardcoded to len(results) and
        # next/previous always null — not actually paginated, even though
        # the spec calls for the standard {count, next, previous, results}
        # paginated shape. Same StandardResultsPagination class every
        # other admin list endpoint in this project already uses.
        paginator = StandardResultsPagination()
        page = paginator.paginate_queryset(payments, request, view=self)

        results = []
        for payment in page:
            order = payment.order
            customer = order.customer

            results.append({
                "order_number": order.order_number,
                "customer": {
                    "id": customer.id,
                    "name": customer.name,
                    "phone": customer.phone,
                },
                "amount": str(order.total_amount),
                "screenshot_url": payment.qr_screenshot_url,
                "transaction_id": payment.qr_transaction_id or "",
                "submitted_at": payment.qr_submitted_at.isoformat() if payment.qr_submitted_at else None,
                "duplicate_warning": payment.qr_duplicate_warning,
            })

        return paginator.get_paginated_response(results)


class AdminQRPaymentApproveView(APIView):
    """
    PUT /api/v1/admin/payments/qr/{order_number}/approve/
    No body.
    Effect:
        payment.status -> paid
        order.status -> confirmed
        total_stock -= qty, reserved_stock -= qty (Transition 2)
        Customer notification
    """
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def put(self, request, order_number):
        try:
            order = Order.objects.get(order_number=order_number)
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        payment = getattr(order, "payment", None)
        if payment is None:
            return Response(
                {"error": "This order has no payment record."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # QR payment only
        if payment.payment_method != "qr":
            return Response(
                {"error": "This order is not a QR payment order."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Must be under_review
        if payment.status != "under_review":
            return Response(
                {"error": f"Payment status is {payment.status}, not under_review."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Order must be pending_payment
        if order.status != "pending_payment":
            return Response(
                {"error": f"Order status is {order.status}, not pending_payment."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # Update payment
            payment.status = "paid"
            payment.paid_at = timezone.now()
            payment.save()

            # Update order
            order.status = "confirmed"
            order.save()

            # ============================================================
            # Transition 2: total_stock -= qty, reserved_stock -= qty
            # ============================================================
            confirm_stock_for_order(order)

        # ============================================================
        # Customer notification (reference_type: "order")
        # ============================================================
        create_notification(
            user=order.customer.user,
            store=order.store,
            title="QR Payment Approved",
            message=f"Your QR payment for order #{order_number} has been approved. Your order is now confirmed.",
            notification_type="system",
            reference_type="order",
            reference_id=order_number,
        )

        return Response({
            "order_number": order_number,
            "payment_status": "paid",
            "order_status": "confirmed",
            "message": "QR payment approved successfully.",
        }, status=status.HTTP_200_OK)


class AdminQRPaymentRejectView(APIView):
    """
    PUT /api/v1/admin/payments/qr/{order_number}/reject/
    Request body: {"reason": "string"} (mandatory)
    Effect:
        payment.status -> rejected
        order.status stays pending_payment (order is NOT cancelled)
        Stock stays reserved (do not release)
        Customer notification includes reason text
    """
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def put(self, request, order_number):
        reason = request.data.get("reason", "").strip()

        if not reason:
            return Response(
                {"error": "reason is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            order = Order.objects.get(order_number=order_number)
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        payment = getattr(order, "payment", None)
        if payment is None:
            return Response(
                {"error": "This order has no payment record."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # QR payment only
        if payment.payment_method != "qr":
            return Response(
                {"error": "This order is not a QR payment order."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Must be under_review
        if payment.status != "under_review":
            return Response(
                {"error": f"Payment status is {payment.status}, not under_review."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # Update payment
            payment.status = "rejected"
            payment.qr_reject_reason = reason
            payment.save()

            # Order status stays pending_payment
            # Stock stays reserved (do not release)

        # ============================================================
        # Customer notification includes reason text
        # ============================================================
        create_notification(
            user=order.customer.user,
            store=order.store,
            title="QR Payment Rejected",
            message=f"Your QR payment for order #{order_number} has been rejected. Reason: {reason}. Please upload a new proof.",
            notification_type="system",
            reference_type="order",
            reference_id=order_number,
        )

        return Response({
            "order_number": order_number,
            "payment_status": "rejected",
            "order_status": order.status,
            "reason": reason,
            "message": "QR payment rejected. Customer can re-upload proof.",
        }, status=status.HTTP_200_OK)