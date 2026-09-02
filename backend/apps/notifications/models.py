from django.db import models
from django.conf import settings


class Notification(models.Model):

    TYPE_CHOICES = [
        ("order", "Order"),
        ("promotion", "Promotion"),
        ("system", "System"),
    ]

    # FIX (B2): aligned with the frontend's actual dropdown values and
    # with standard notification-channel naming (in_app / email / sms -
    # same convention used by most notification platforms). 'web' is
    # renamed to 'in_app' (same concept, just the standard name);
    # 'whatsapp' is dropped - grep across the whole codebase confirmed
    # nothing ever sets sent_via='whatsapp', it was a declared-but-
    # unused choice. If WhatsApp delivery is actually needed later
    # (there's already a whatsapp app in this project), it's a
    # one-line addition back to this list + a migration.
    SENT_VIA_CHOICES = [
        ("in_app", "In-App"),
        ("email", "Email"),
        ("sms", "SMS"),
    ]

    # ============================================================
    # NEW: Reference type choices as per PDF Part 2 Item 3
    # ============================================================
    REFERENCE_TYPE_CHOICES = [
        ("order", "Order"),
        ("return", "Return"),
        ("complaint", "Complaint"),
    ]

    store = models.ForeignKey(
        "stores.Store",
        on_delete=models.CASCADE,
        related_name="notifications",
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
    )

    title = models.CharField(max_length=255)

    message = models.TextField()

    type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default="system",
    )

    is_read = models.BooleanField(default=False)

    sent_via = models.CharField(
        max_length=20,
        choices=SENT_VIA_CHOICES,
        default="in_app",
    )

    # ============================================================
    # NEW FIELDS: Deep linking as per PDF Part 2 Item 3
    # ============================================================
    reference_type = models.CharField(
        max_length=20,
        choices=REFERENCE_TYPE_CHOICES,
        null=True,
        blank=True,
        help_text="Type of entity this notification references: order, return, or complaint"
    )

    reference_id = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="ID of the referenced entity (order_number, return_id, complaint_id)"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "notifications"
        ordering = ["-created_at"]

    def __str__(self):
        ref_info = ""
        if self.reference_type and self.reference_id:
            ref_info = f" [{self.reference_type}:{self.reference_id}]"
        return f"{self.title}{ref_info} → {self.user.email if self.user else 'broadcast'}"