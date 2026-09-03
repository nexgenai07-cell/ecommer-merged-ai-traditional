# PATH: apps/returns/complaint_serializers.py

from rest_framework import serializers
from apps.orders.models import Order
from .models import Complaint, ComplaintMessage


class ComplaintSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(
        source="customer.name",
        read_only=True,
    )

    order_number = serializers.CharField(
        source="order.order_number",
        read_only=True,
        default=None,
    )

    resolved_by_name = serializers.CharField(
        source="resolved_by.name",
        read_only=True,
        default=None,
    )

    attachment = serializers.SerializerMethodField()

    class Meta:
        model = Complaint
        fields = [
            "id",
            "customer",
            "customer_name",
            "order",
            "order_number",
            "message",
            "type",
            "status",
            "priority",
            "attachment",
            "response",
            "resolved_by_name",
            "created_at",
        ]

        read_only_fields = [
            "id",
            "status",
            "response",
            "created_at",
        ]

    def get_attachment(self, obj):
        if not obj.attachment:
            return None

        request = self.context.get("request")

        if request:
            return request.build_absolute_uri(obj.attachment.url)

        return obj.attachment.url


class CreateComplaintSerializer(serializers.ModelSerializer):
    # Accept order_number instead of primary key
    order = serializers.SlugRelatedField(
        slug_field="order_number",
        queryset=Order.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Complaint
        fields = [
            "type",
            "order",
            "message",
            "priority",
            "attachment",
        ]

    def validate_attachment(self, value):
        if not value:
            return value

        if value.size > 10 * 1024 * 1024:
            raise serializers.ValidationError(
                "File too large."
            )

        allowed_types = [
            "image/png",
            "image/jpeg",
            "application/pdf",
        ]

        if value.content_type not in allowed_types:
            raise serializers.ValidationError(
                "Unsupported file type."
            )

        return value


class AdminComplaintStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            "open",
            "in_progress",
            "resolved",
            "closed",
        ]
    )


# NEW (Backend Change Request v2, Part 2 — Item 4 / Issue 7):
# AdminComplaintRespondSerializer removed along with PUT
# /api/v1/admin/complaints/{id}/respond/ — replaced by the two below.
#
# GET /api/v1/complaints/{id}/messages/ response shape, per spec:
# { "id", "sender": "customer"|"admin", "message", "created_at" }
class ComplaintMessageSerializer(serializers.ModelSerializer):
    sender = serializers.CharField(read_only=True)

    class Meta:
        model = ComplaintMessage
        fields = ["id", "sender", "message", "created_at"]
        read_only_fields = fields


# POST /api/v1/complaints/{id}/messages/ request body: {"message": "string"}
class CreateComplaintMessageSerializer(serializers.Serializer):
    message = serializers.CharField()

    def validate_message(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Message cannot be blank.")
        return value