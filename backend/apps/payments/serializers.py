from rest_framework import serializers
from apps.orders.models import Payment


class PaymentSerializer(serializers.ModelSerializer):
    """Basic payment serializer"""

    # FIX (Cross-check, Sep 2026): spec locks this field's JSON name as
    # "method" (payment.method), not payment_method — see
    # orders/serializers.py PaymentSerializer for the full note.
    method = serializers.CharField(source="payment_method")

    class Meta:
        model = Payment
        fields = [
            'id',
            'status',
            'amount',
            'method',
            'paid_at',
            'refunded_at',
            'refund_method',
            'refund_transaction_reference',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class PaymentDetailSerializer(serializers.ModelSerializer):
    """Detailed payment serializer with QR fields"""

    method = serializers.CharField(source="payment_method")

    class Meta:
        model = Payment
        fields = [
            'id',
            'status',
            'amount',
            'method',
            'paid_at',
            'refunded_at',
            'refund_method',
            'refund_transaction_reference',
            'qr_screenshot_url',
            'qr_transaction_id',
            'qr_submitted_at',
            'qr_reject_reason',
            'qr_image_hash',
            'qr_duplicate_warning',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class QRProofUploadSerializer(serializers.Serializer):
    """Serializer for QR proof upload"""
    order_number = serializers.CharField(required=True)
    screenshot = serializers.FileField(required=True)
    transaction_id = serializers.CharField(required=False, allow_blank=True)


class QRRejectSerializer(serializers.Serializer):
    """Serializer for QR payment rejection"""
    reason = serializers.CharField(required=True)