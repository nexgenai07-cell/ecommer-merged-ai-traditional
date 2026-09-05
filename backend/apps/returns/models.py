from django.core.exceptions import ValidationError
from django.db import models
from django.conf import settings


class Return(models.Model):
    """Customer return request linked to a delivered order."""

    STATUS_CHOICES = [
        ("requested", "Requested"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("completed", "Completed"),
    ]

    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="returns",
    )

    customer = models.ForeignKey(
        "orders.Customer",
        on_delete=models.CASCADE,
        related_name="returns",
        null=True,
        blank=True,
        help_text="Auto-filled from the linked order.",
    )

    reason = models.TextField()

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="requested",
    )

    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Time the return was approved, rejected, or completed.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "returns"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Return for {self.order.order_number}"

    # Ensures customer is always linked to the same customer as the order.
    def save(self, *args, **kwargs):
        if not self.customer and self.order:
            self.customer = self.order.customer

        super().save(*args, **kwargs)


class Complaint(models.Model):
    """Customer complaint, optionally linked to an order."""

    STATUS_CHOICES = [
        ("open", "Open"),
        ("in_progress", "In Progress"),
        ("resolved", "Resolved"),
        ("closed", "Closed"),
    ]

    TYPE_CHOICES = [
        ("order", "Order Issue"),
        ("payment", "Payment Issue"),
        ("product", "Product Issue"),
        ("delivery", "Delivery Issue"),
        ("other", "Other"),
    ]

    PRIORITY_CHOICES = [
        ("normal", "Normal"),
        ("urgent", "Urgent"),
    ]

    customer = models.ForeignKey(
        "orders.Customer",
        on_delete=models.CASCADE,
        related_name="complaints",
    )

    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="complaints",
    )

    message = models.TextField()

    type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default="other",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="open",
    )

    priority = models.CharField(
        max_length=10,
        choices=PRIORITY_CHOICES,
        default="normal",
    )

    attachment = models.FileField(
        upload_to="complaints/attachments/%Y/%m/",
        null=True,
        blank=True,
    )

    response = models.TextField(
        null=True,
        blank=True,
        help_text="Legacy admin response to the customer.",
    )

    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_complaints",
        help_text="Admin who resolved the complaint.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "complaints"
        ordering = ["-created_at"]

    # Prevents associating an order with a different customer.
    def clean(self):
        if (
            self.order
            and self.customer
            and self.order.customer_id != self.customer_id
        ):
            raise ValidationError(
                "Selected order does not belong to this customer."
            )

    # Runs ownership validation before persisting the complaint.
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Complaint by {self.customer.name} [{self.status}]"


class ComplaintMessage(models.Model):
    """One private message in a complaint thread."""

    complaint = models.ForeignKey(
        Complaint,
        on_delete=models.CASCADE,
        related_name="messages",
    )

    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="complaint_messages",
    )

    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "complaint_messages"
        ordering = ["created_at"]