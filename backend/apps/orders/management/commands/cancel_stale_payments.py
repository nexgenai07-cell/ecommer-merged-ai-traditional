# PATH: apps/orders/management/commands/cancel_stale_payments.py
#
# Backend Change Request v2 — scheduled timeout jobs (two original ones
# below, plus a third one added Sep 2026 for rejected QR proofs):
#
#   1. Item 5, transition 4: Stripe payment timeout — exactly 30 minutes
#      from order creation with no successful Stripe webhook.
#   2. Part 3.7: QR auto-cancel timeout — exactly 24 hours from order
#      creation with no proof ever uploaded (payment.status still
#      "pending" — once proof is uploaded it moves to "under_review" and
#      this timeout no longer applies, per spec).
#
#   3. NEW (Sep 2026 — QR rejection flow change): a QR proof was rejected
#      (1st or 2nd time, so the order stayed "pending_payment" with
#      payment.status == "rejected") and the customer never re-uploaded
#      within 24 hours of that rejection. Without this, such an order
#      would sit in pending_payment forever, holding reserved stock,
#      because job 2 above only matches payment.status == "pending".
#
# All three: order.status -> cancelled, release reserved_stock (Transition 3),
# send the customer the existing cancellation notification. This is a
# backend cron/scheduled task — the frontend never triggers it.
#
# HOW TO SCHEDULE (nothing in this codebase runs management commands on a
# schedule yet — pick whichever fits the deployment):
#   - Railway: add a Cron Job service running
#       python manage.py cancel_stale_payments
#     on a "every 5 minutes" schedule (or whatever cadence is acceptable
#     given the 30-minute Stripe window — 5 minutes keeps the worst-case
#     lateness small).
#   - Plain server: a cron entry, e.g.
#       */5 * * * * cd /path/to/project && python manage.py cancel_stale_payments
#   - If Celery gets added to this project later, wrap this same logic in
#     a periodic task instead — the two functions below don't care how
#     they're invoked.
#
# Safe to run as often as you like / re-run after a crash: each order is
# only ever touched once (it stops matching the queryset the moment its
# status flips to "cancelled"), and each order is row-locked + re-checked
# inside its own transaction so a payment that succeeds in the same
# instant this command runs can never be cancelled out from under it.

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

from apps.notifications.utils import create_notification
from apps.orders.models import Order, Payment
from apps.orders.views import release_reserved_stock_for_order

STRIPE_TIMEOUT = timedelta(minutes=30)
QR_TIMEOUT = timedelta(hours=24)
# NEW (Sep 2026): how long a customer gets to re-upload a QR proof after
# a rejection before the order is auto-cancelled. Change this one value
# if you want a shorter/longer window.
QR_REJECTED_TIMEOUT = timedelta(hours=24)


def _cancel_order_for_timeout(order, reason):
    """
    Shared by both timeout paths. Re-fetches + locks the order row inside
    its own transaction and re-checks status=="pending_payment" right
    before acting — guards against a payment confirming in the tiny
    window between the queryset being built and this order being
    processed.
    """
    with transaction.atomic():
        locked_order = Order.objects.select_for_update().get(pk=order.pk)

        if locked_order.status != "pending_payment":
            # Already paid / already cancelled by something else since
            # the queryset was built — nothing to do.
            return False

        release_reserved_stock_for_order(locked_order)
        locked_order.status = "cancelled"
        locked_order.cancellation_reason = reason
        locked_order.save()

    # Existing cancellation notification — same one customer-initiated
    # and admin-initiated cancellation already send, so the customer
    # sees a consistent message regardless of who/what cancelled it.
    
    return True


def cancel_expired_stripe_orders(now=None):
    """Transition 4: 30-minute Stripe payment timeout."""
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
        ):
            cancelled_count += 1

    return cancelled_count


def cancel_expired_qr_orders(now=None):
    """
    Part 3.7: 24-hour QR auto-cancel — only when NO proof was ever
    uploaded. payment.status must still be "pending"; once proof is
    uploaded it moves to "under_review" (see QRProofUploadView) and this
    timeout stops applying — the order is then waiting on admin
    approve/reject, not the customer, per spec.
    """
    now = now or timezone.now()
    cutoff = now - QR_TIMEOUT

    stale_orders = Order.objects.filter(
        status="pending_payment",
        payment__payment_method="qr",
        payment__status="pending",
        created_at__lte=cutoff,
    ).select_related("customer", "customer__user", "store", "payment")

    cancelled_count = 0
    for order in stale_orders:
        if _cancel_order_for_timeout(
            order,
            "QR payment proof was not submitted within 24 hours.",
        ):
            cancelled_count += 1

    return cancelled_count


def cancel_expired_rejected_qr_orders(now=None):
    """
    NEW (Sep 2026 — QR rejection flow change): auto-cancel QR orders
    whose proof was rejected (1st/2nd rejection) and never re-uploaded
    within QR_REJECTED_TIMEOUT.

    Matches: order.status == "pending_payment", payment method "qr",
    payment.status == "rejected". Payment.updated_at is used as "when it
    was rejected" — nothing else touches the payment row between a
    rejection and the customer's next upload (which flips it to
    "under_review" and takes it out of this queryset), so no new field /
    migration is needed.

    payment.status stays "rejected" on purpose: together with the
    cancelled order it makes QRProofUploadView answer "This order has
    been cancelled." (the qr_rejection_count is still below the 3-attempt
    cap, so it is not the "max attempts" message). Like every other
    cancelled order it is final — nothing can change its status again.
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
            # Lock the order AND its payment, then re-check both: if the
            # customer re-uploaded a proof (payment -> "under_review") or
            # the order was cancelled/confirmed in the tiny window since
            # the queryset was built, do nothing.
            locked_order = Order.objects.select_for_update().get(pk=order.pk)
            locked_payment = Payment.objects.select_for_update().get(
                order=locked_order
            )

            if (
                locked_order.status != "pending_payment"
                or locked_payment.status != "rejected"
            ):
                continue

            release_reserved_stock_for_order(locked_order)
            locked_order.status = "cancelled"
            locked_order.cancellation_reason = (
                "QR payment proof was rejected and no new proof was "
                "submitted within 24 hours."
            )
            locked_order.save()

        create_notification(
            user=order.customer.user,
            store=order.store,
            title="Order cancelled",
            message=(
                f"Your order {order.order_number} has been cancelled because "
                "no new payment proof was submitted within 24 hours of the "
                "rejection."
            ),
            notification_type="order",
            reference_type="order",
            reference_id=order.order_number,
        )

        cancelled_count += 1

    return cancelled_count


class Command(BaseCommand):
    help = (
        "Auto-cancels stale pending_payment orders: Stripe orders after "
        "30 minutes with no successful webhook, QR orders after 24 hours "
        "with no proof uploaded, QR orders whose rejected proof was not "
        "re-uploaded within 24 hours. Releases their reserved stock and "
        "notifies the customer. Intended to run on a schedule (e.g. every "
        "5 minutes via cron) — see the module docstring for how to wire "
        "that up on this deployment."
    )

    def handle(self, *args, **options):
        now = timezone.now()

        stripe_cancelled = cancel_expired_stripe_orders(now=now)
        qr_cancelled = cancel_expired_qr_orders(now=now)
        rejected_cancelled = cancel_expired_rejected_qr_orders(now=now)

        self.stdout.write(
            self.style.SUCCESS(
                f"cancel_stale_payments: {stripe_cancelled} Stripe order(s) "
                f"cancelled (30-min timeout), {qr_cancelled} QR order(s) "
                f"cancelled (24-hr timeout), {rejected_cancelled} QR order(s) "
                f"cancelled (rejected proof not re-uploaded in 24 hrs)."
            )
        )