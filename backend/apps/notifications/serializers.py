from rest_framework import serializers
from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            'id', 
            'title', 
            'message', 
            'type', 
            'is_read',
            'sent_via', 
            'created_at',
            # ============================================================
            # NEW FIELDS: Deep linking as per PDF Part 2 Item 3
            # ============================================================
            'reference_type',
            'reference_id',
        ]
        read_only_fields = ['id', 'created_at']