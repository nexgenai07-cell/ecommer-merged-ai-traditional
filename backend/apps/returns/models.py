# PATH: apps/returns/models.py

from django.core.exceptions import ValidationError
from django.db import models
from django.conf import settings
from django.utils import timezone


class Return(models.Model):
    """Customer return request linked to a delivered order."""

    # UPDATED (Sep 2026): only three statuses now.
    #   - "requested" was renamed back to "pending".
    #   - "completed" was removed (old completed rows were migrated to
    #     "approved" — see migration 0007).
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    # The only status an admin can still act on.
    PENDING_STATUS = "pending"

    # Once a return lands in one of these, it's a final admin decision —
    # it can never change again (not approved -> rejected, not
    # rejected -> approved, and not back to pending).
    RESOLVED_STATUSES = {"approved", "rejected"}

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
        default="pending",
    )

    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Time the return was approved or rejected.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "returns"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Return for {self.order.order_number}"

    # NEW (Sep 2026): the admin can only update a return while it is
    # still pending, and only to approved or rejected. Exposed on the
    # return API response (see ReturnSerializer) so the frontend knows
    # whether to show the "Update Status" button and which options to
    # offer.
    @property
    def can_update_status(self):
        return self.status == self.PENDING_STATUS

    @property
    def allowed_statuses(self):
        if self.can_update_status:
            return ["approved", "rejected"]
        return []

    # Locks the decision once an admin has approved/rejected a return:
    # a resolved status can never be changed to a different status
    # (in particular, approved <-> rejected is blocked).
    def clean(self):
        if self.pk:
            try:
                old_status = Return.objects.only("status").get(pk=self.pk).status
            except Return.DoesNotExist:
                old_status = None

            if old_status in self.RESOLVED_STATUSES and self.status != old_status:
                raise ValidationError(
                    {
                        "status": (
                            f"This return has already been {old_status} "
                            "and its status cannot be changed."
                        )
                    }
                )

    # Ensures customer is always linked to the same customer as the order,
    # enforces the status-lock above, and stamps resolved_at the moment
    # a return is finalized.
    def save(self, *args, **kwargs):
        if not self.customer and self.order:
            self.customer = self.order.customer

        self.full_clean()

        if self.status in self.RESOLVED_STATUSES and not self.resolved_at:
            self.resolved_at = timezone.now()

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