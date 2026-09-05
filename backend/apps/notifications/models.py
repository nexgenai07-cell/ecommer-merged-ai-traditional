from django.db import models
from django.conf import settings


class Notification(models.Model):

    TYPE_CHOICES = [
        ("order", "Order"),
        ("promotion", "Promotion"),
        ("system", "System"),
    ]

    SENT_VIA_CHOICES = [
        ("whatsapp", "WhatsApp"),
        ("email", "Email"),
        ("web", "Web"),
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