import stripe
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.utils import timezone

from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import (
    authentication_classes,
    permission_classes,
)

from apps.orders.models import Order

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
            payment.status = "paid"
            payment.paid_at = timezone.now()
            payment.save()

            order.status = "confirmed"
            order.save()

            return Response(
                {
                    "free_order": True,
                    "message": "Order total is Rs. 0 after discount — no payment needed.",
                    "order_number": order.order_number,
                }
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
        return Response(
            {
                "client_secret": intent.client_secret,
                "publishable_key": settings.STRIPE_PUBLISHABLE_KEY,
                "amount": amount_in_paisa,
                "currency": "pkr",
                "subtotal": str(total_amount + order.discount_amount),
                "discount_amount": str(order.discount_amount),
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

                payment = order.payment
                payment.status = "paid"
                payment.paid_at = timezone.now()
                payment.save()

                order.status = "confirmed"
                order.save()

            except Order.DoesNotExist:
                pass

        elif event["type"] == "payment_intent.payment_failed":
            intent = event["data"]["object"]

            order_number = intent["metadata"].get("order_number")

            try:
                order = Order.objects.get(order_number=order_number)

                payment = order.payment
                payment.status = "failed"
                payment.save()

            except Order.DoesNotExist:
                pass

        return Response(status=200)