# PATH: apps/orders/management/commands/cancel_stale_payments.py
#
# Scheduled timeout jobs (run every 60 seconds by apps/orders/scheduler.py,
# or manually: python manage.py cancel_stale_payments).
#
# Current jobs:
#   1. Stripe payment timeout — 30 minutes from order creation with no
#      successful Stripe webhook.
#   2. QR first-proof window — order still "order_placed" (no proof ever
#      uploaded) once payment.qr_upload_deadline passes. That deadline is
#      10 minutes from checkout, or 5 minutes from the moment the customer
#      pressed the one-time "need more time?" button (ExtendQRUploadTimeView).
#      There is NO 24-hour rule for this anymore.
#   3. QR rejected-proof re-upload window — order "pending_payment" with
#      payment.status == "rejected" (1st/2nd rejection): the customer gets
#      1 HOUR (QR_REJECTED_TIMEOUT) from the rejection (payment.updated_at)
#      to upload a new proof.
#
# All three: order.status -> cancelled, release reserved_stock, notify the
# customer (in-app notification + email).
#
# UPDATED (Sep 2026): the Stripe timeout path used to cancel the order and
# release stock but never actually sent the customer notification/email
# (only a comment claimed it did). It now sends both, like the QR paths.
#
# Safe to run as often as you like / concurrently: each order is
# row-locked and re-checked inside its own transaction, and stops matching
# the queryset the moment its status becomes "cancelled".

import logging

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

from apps.notifications.utils import create_notification
from apps.orders.models import Order, Payment, OrderStatusHistory
from apps.orders.status_email import send_order_status_email
from apps.orders.views import release_reserved_stock_for_order

logger = logging.getLogger(__name__)

STRIPE_TIMEOUT = timedelta(minutes=30)

# How long a customer gets to re-upload a QR proof after an admin
# rejection before the order is auto-cancelled. Change this one value to
# make the window shorter/longer.
QR_REJECTED_TIMEOUT = timedelta(hours=1)


def _cancel_order_for_timeout(order, reason, customer_message):
    """
    Used by the Stripe timeout path. Re-fetches + locks the order row
    inside its own transaction and re-checks status == "pending_payment"
    right before acting — guards against a payment confirming in the tiny
    window between the queryset being built and this order being
    processed. Sends the customer notification + email after commit.
    """
    with transaction.atomic():
        locked_order = Order.objects.select_for_update().get(pk=order.pk)

        if locked_order.status != "pending_payment":
            # Already paid / already cancelled by something else.
            return False

        release_reserved_stock_for_order(locked_order)
        locked_order.status = "cancelled"
        locked_order.cancellation_reason = reason
        locked_order.save()

        OrderStatusHistory.record(locked_order, "cancelled", note=reason)

    _notify_customer(order, customer_message)
    return True


def _notify_customer(order, message):
    """In-app notification + email. Never lets a failure here break the job."""
    try:
        create_notification(
            user=order.customer.user,
            store=order.store,
            title="Order cancelled",
            message=message,
            notification_type="order",
            reference_type="order",
            reference_id=order.order_number,
        )
    except Exception:
        logger.exception("cancel_stale_payments: notification failed")

    try:
        send_order_status_email(order, "Order cancelled", message)
    except Exception:
        logger.exception("cancel_stale_payments: status email failed")


def cancel_expired_stripe_orders(now=None):
    """30-minute Stripe payment timeout."""
    now = now or timezone.now()
    cutoff = now - STRIPE_TIMEOUT

    stale_orders = Order.objects.filter(
        status="pending_payment",
        payment__payment_method="stripe",
        created_at__lte=cutoff,
    ).select_related("customer", "customer__user", "store", "payment")

    cancelled_count = 0
    for order in stale_orders:
        if _cancel_order_for_timeout(
            order,
            "Payment timeout — no payment received within 30 minutes.",
            (
                f"Your order {order.order_number} has been cancelled "
                "because payment was not received within 30 minutes."
            ),
        ):
            cancelled_count += 1

    return cancelled_count


def cancel_expired_qr_placements(now=None):
    """
    QR first-proof window (10 minutes, or +5 minutes if extended).

    Matches orders still "order_placed" (no proof ever uploaded) whose own
    payment.qr_upload_deadline has passed. Once proof is uploaded,
    QRProofUploadView moves the order to "pending_payment" and this
    function no longer matches it.
    """
    now = now or timezone.now()

    stale_orders = Order.objects.filter(
        status="order_placed",
        payment__payment_method="qr",
        payment__qr_upload_deadline__lte=now,
    ).select_related("customer", "customer__user", "store", "payment")

    cancelled_count = 0
    for order in stale_orders:
        with transaction.atomic():
            locked_order = Order.objects.select_for_update().get(pk=order.pk)
            locked_payment = Payment.objects.select_for_update().get(
                order=locked_order
            )

            if (
                locked_order.status != "order_placed"
                or not locked_payment.qr_upload_deadline
                or locked_payment.qr_upload_deadline > now
            ):
                # Proof arrived, or the deadline moved (extension request
                # racing this job), since the queryset was built.
                continue

            release_reserved_stock_for_order(locked_order)
            locked_order.status = "cancelled"
            locked_order.cancellation_reason = (
                "QR payment proof was not submitted within the allowed "
                "time window."
            )
            locked_order.save()

            OrderStatusHistory.record(
                locked_order, "cancelled", note=locked_order.cancellation_reason
            )

        _notify_customer(
            order,
            (
                f"Your order {order.order_number} has been cancelled "
                "because payment proof was not submitted in time."
            ),
        )

        cancelled_count += 1

    return cancelled_count


def cancel_expired_rejected_qr_orders(now=None):
    """
    Auto-cancel QR orders whose proof was rejected (1st/2nd rejection) and
    never re-uploaded within QR_REJECTED_TIMEOUT (1 hour).

    Matches: order.status == "pending_payment", payment method "qr",
    payment.status == "rejected". Payment.updated_at is used as "when it
    was rejected" — nothing else touches the payment row between a
    rejection and the customer's next upload (which flips it to
    "under_review" and takes it out of this queryset).

    payment.status stays "rejected" on purpose: together with the
    cancelled order it makes QRProofUploadView answer "This order has been
    cancelled." Like every other cancelled order it is final.
    """
    now = now or timezone.now()
    cutoff = now - QR_REJECTED_TIMEOUT

    stale_orders = Order.objects.filter(
        status="pending_payment",
        payment__payment_method="qr",
        payment__status="rejected",
        payment__updated_at__lte=cutoff,
    ).select_related("customer", "customer__user", "store", "payment")

    cancelled_count = 0
    for order in stale_orders:
        with transaction.atomic():
            locked_order = Order.objects.select_for_update().get(pk=order.pk)
            locked_payment = Payment.objects.select_for_update().get(
                order=locked_order
            )

            if (
                locked_order.status != "pending_payment"
                or locked_payment.status != "rejected"
            ):
                # Customer re-uploaded, or order was cancelled/confirmed
                # in the tiny window since the queryset was built.
                continue

            release_reserved_stock_for_order(locked_order)
            locked_order.status = "cancelled"
            locked_order.cancellation_reason = (
                "QR payment proof was rejected and no new proof was "
                "submitted within 1 hour."
            )
            locked_order.save()

            OrderStatusHistory.record(
                locked_order, "cancelled", note=locked_order.cancellation_reason
            )

        _notify_customer(
            order,
            (
                f"Your order {order.order_number} has been cancelled because "
                "no new payment proof was submitted within 1 hour of the "
                "rejection."
            ),
        )

        cancelled_count += 1

    return cancelled_count


class Command(BaseCommand):
    help = (
        "Auto-cancels stale orders: Stripe orders after 30 minutes with no "
        "successful webhook, QR orders still 'order_placed' whose 10 (or "
        "15, if extended) minute upload window ran out, and QR orders whose "
        "rejected proof was not re-uploaded within 1 hour. Releases their "
        "reserved stock and notifies the customer. Runs automatically every "
        "60 seconds via apps/orders/scheduler.py — can also be run manually."
    )

    def handle(self, *args, **options):
        now = timezone.now()

        placement_cancelled = cancel_expired_qr_placements(now=now)
        stripe_cancelled = cancel_expired_stripe_orders(now=now)
        rejected_cancelled = cancel_expired_rejected_qr_orders(now=now)

        self.stdout.write(
            self.style.SUCCESS(
                f"cancel_stale_payments: {placement_cancelled} QR order(s) "
                f"cancelled (10/15-min upload window), {stripe_cancelled} "
                f"Stripe order(s) cancelled (30-min timeout), "
                f"{rejected_cancelled} QR order(s) cancelled (rejected "
                f"proof not re-uploaded within 1 hour)."
            )
        )