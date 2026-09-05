from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.orders.views import restore_stock_for_order
from .models import QRPaymentProof


# Cancels QR orders that have waited at least 24 hours without any proof.
# Each proof is marked expired, so a later scheduled run cannot notify twice.
def cancel_expired_qr_orders(now=None):
    deadline = (now or timezone.now()) - timedelta(hours=24)

    expired_proofs = QRPaymentProof.objects.select_related(
        "order__customer__user",
        "order__store",
    ).filter(
        status="awaiting_proof",
        created_at__lte=deadline,
        order__status="pending_payment",
    )

    cancelled_count = 0

    for proof in expired_proofs:
        with transaction.atomic():
            # Locks the proof before checking again, preventing duplicate
            # cancellation/notifications when two scheduled jobs overlap.
            proof = (
                QRPaymentProof.objects.select_for_update()
                .select_related(
                    "order__customer__user",
                    "order__store",
                )
                .get(pk=proof.pk)
            )

            order = proof.order

            if (
                proof.status != "awaiting_proof"
                or order.status != "pending_payment"
            ):
                continue

            restore_stock_for_order(order, user=None)

            order.status = "cancelled"
            order.save(update_fields=["status", "updated_at"])

            proof.status = "expired"
            proof.resolved_at = timezone.now()
            proof.save(update_fields=["status", "resolved_at"])

            

            cancelled_count += 1

    return cancelled_count