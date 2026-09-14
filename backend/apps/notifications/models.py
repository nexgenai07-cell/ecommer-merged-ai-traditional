# PATH: apps/notifications/models.py

from django.db import models
from django.conf import settings


class Notification(models.Model):

    TYPE_CHOICES = [
        ("order", "Order"),
        ("promotion", "Promotion"),
        ("system", "System"),
    ]

    # FIX (Sep 2026 — Send Notification "Channel" bug): "in_app" was
    # missing here even though the frontend's Channel dropdown sends it
    # and utils.create_notification()'s own default sent_via value is
    # "in_app" — the model just never had a matching choice. This was
    # harmless before only because Notification.objects.create()/.save()
    # don't enforce `choices` at the DB layer, so "in_app" silently saved
    # anyway. It stops being harmless now that SendNotificationView
    # validates sent_via against this exact list (see views.py) — without
    # this, every "In-App" channel request would get wrongly rejected.
    SENT_VIA_CHOICES = [
        ("whatsapp", "WhatsApp"),
        ("email", "Email"),
        ("web", "Web"),
        ("in_app", "In-App"),
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
    reference_type = models.CharField(
        max_length=20,
        choices=[
            ("order", "Order"),
            ("return", "Return"),
            ("complaint", "Complaint"),
            ("product", "Product"),
        ],
        null=True,
        blank=True,
    )

    reference_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
    )

    is_read = models.BooleanField(default=False)

    sent_via = models.CharField(
        max_length=20,
        choices=SENT_VIA_CHOICES,
        default="web",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "notifications"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} → {self.user.email if self.user else 'broadcast'}"