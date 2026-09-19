# PATH: apps/returns/serializers.py

from rest_framework import serializers
from .models import Return, Complaint, ComplaintMessage


class ReturnSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source='order.order_number', read_only=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True)

    # NEW (Sep 2026): drives the admin "Update Status" button on the return
    # detail screen. Only ever true/non-empty for an admin looking at a
    # return that is still "pending"; for approved/rejected returns (and
    # for customers) can_update_status is False and allowed_statuses is [].
    can_update_status = serializers.SerializerMethodField()
    allowed_statuses = serializers.SerializerMethodField()

    class Meta:
        model = Return
        fields = [
            'id', 'order', 'order_number', 'customer', 'customer_name',
            'reason', 'status', 'resolved_at', 'created_at',
            'can_update_status', 'allowed_statuses',
        ]
        read_only_fields = ['id', 'status', 'resolved_at', 'created_at']

    def _request_user_is_admin(self):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        return bool(
            user
            and user.is_authenticated
            and getattr(user, 'role', None) == 'admin'
        )

    def get_can_update_status(self, obj):
        return self._request_user_is_admin() and obj.can_update_status

    def get_allowed_statuses(self, obj):
        if self._request_user_is_admin():
            return obj.allowed_statuses
        return []


class CreateReturnSerializer(serializers.Serializer):
    reason = serializers.CharField()


class AdminReturnStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['approved', 'rejected'])


# ============================================================
# NEW: Complaint Serializers as per PDF Part 2 Item 4
# ============================================================

class ComplaintMessageSerializer(serializers.ModelSerializer):
    """Serializer for complaint messages"""

    class Meta:
        model = ComplaintMessage
        fields = [
            'id',
            'sender',
            'message',
            'created_at',
        ]
        read_only_fields = ['id', 'sender', 'created_at']


class ComplaintMessageCreateSerializer(serializers.Serializer):
    """Serializer for creating a new complaint message"""
    message = serializers.CharField(required=True, allow_blank=False)


class ComplaintSerializer(serializers.ModelSerializer):
    """Basic complaint serializer for list views"""
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    order_number = serializers.CharField(source='order.order_number', read_only=True, allow_null=True)

    class Meta:
        model = Complaint
        fields = [
            'id',
            'customer',
            'customer_name',
            'order',
            'order_number',
            'message',
            'type',
            'status',
            'priority',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class ComplaintDetailSerializer(serializers.ModelSerializer):
    """Detailed complaint serializer with messages thread"""
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    order_number = serializers.CharField(source='order.order_number', read_only=True, allow_null=True)
    messages = ComplaintMessageSerializer(many=True, read_only=True)

    class Meta:
        model = Complaint
        fields = [
            'id',
            'customer',
            'customer_name',
            'order',
            'order_number',
            'message',
            'type',
            'status',
            'priority',
            'attachment',
            'created_at',
            'updated_at',
            'messages',  # Full thread
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'messages']


class ComplaintStatusUpdateSerializer(serializers.Serializer):
    """Serializer for admin status update"""
    status = serializers.ChoiceField(choices=Complaint.STATUS_CHOICES)