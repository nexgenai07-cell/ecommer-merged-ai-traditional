from django.db import models


class QRPaymentProof(models.Model):
    """
    Stores the state of a QR payment proof for an order.
    The upload endpoint should change status to "submitted".
    """

    STATUS_CHOICES = [
        ("awaiting_proof", "Awaiting proof"),
        ("submitted", "Submitted"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("expired", "Expired"),
    ]

    order = models.OneToOneField(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="qr_payment_proof",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="awaiting_proof",
    )

    rejection_reason = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    uploaded_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "qr_payment_proofs"