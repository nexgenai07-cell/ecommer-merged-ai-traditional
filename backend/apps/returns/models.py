# PATH: apps/returns/models.py
from django.core.exceptions import ValidationError
from django.db import models
from django.conf import settings


class Return(models.Model):
    """
    Customer return requests for delivered orders.
    Only delivered orders can have return requests.
    customer field added for direct link without going through order.
    resolved_at tracks when the return was completed.
    """

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
        help_text="Direct link to customer — auto-filled from order on save",
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
        help_text="Timestamp when status changed to approved/rejected/completed",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "returns"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Return for {self.order.order_number}"

    def save(self, *args, **kwargs):
        if not self.customer and self.order:
            self.customer = self.order.customer
        super().save(*args, **kwargs)


class Complaint(models.Model):
    """
    Customer complaints linked to orders or general issues.
    """

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

    # ============================================================
    # DEPRECATED: 'response' field - ab messages system use karenge
    # Keep for backward compatibility, but not used for new flow
    # ============================================================
    response = models.TextField(
        null=True,
        blank=True,
        help_text="DEPRECATED: Use ComplaintMessage system instead",
    )

    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_complaints",
        help_text="Admin who resolved this complaint",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "complaints"
        ordering = ["-created_at"]

    def clean(self):
        if self.order and self.customer:
            if self.order.customer_id != self.customer_id:
                raise ValidationError(
                    "Selected order does not belong to this customer."
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Complaint by {self.customer.name} [{self.status}]"


# ============================================================
# NEW: Complaint Message Model as per PDF Part 2 Item 4
# ============================================================
class ComplaintMessage(models.Model):
    """
    Individual messages in a complaint thread.
    Both customer and admin can send messages.
    Sending a message NEVER changes complaint status.
    """

    SENDER_CHOICES = [
        ("customer", "Customer"),
        ("admin", "Admin"),
    ]

    complaint = models.ForeignKey(
        Complaint,
        on_delete=models.CASCADE,
        related_name="messages",
    )

    sender = models.CharField(
        max_length=20,
        choices=SENDER_CHOICES,
        help_text="Who sent this message: customer or admin"
    )

    sender_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="User who sent this message (admin user or customer's user)"
    )

    message = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "complaint_messages"
        ordering = ["created_at"]  # Chronological order

    def __str__(self):
        return f"Message #{self.id} on Complaint #{self.complaint_id} from {self.sender}"