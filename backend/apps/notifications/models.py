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

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "notifications"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} → {self.user.email if self.user else 'broadcast'}"