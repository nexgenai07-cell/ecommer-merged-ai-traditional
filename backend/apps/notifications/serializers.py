from zoneinfo import ZoneInfo

from django.utils import timezone
from rest_framework import serializers
from .models import Notification
from .customer_names import customer_names_for

# FIX: notification date/time was showing wrong — created_at was being
# serialized using whatever settings.TIME_ZONE the project happens to be
# running with (Django defaults to UTC), so a notification actually sent
# at, say, 11:45 PM Pakistan time showed up as 6:45 PM the same day (fine)
# but one sent at 2:00 AM PKT showed up as 9:00 PM the PREVIOUS day (wrong
# date, not just wrong time). Pinning the conversion to Asia/Karachi here
# means the API always returns the real local date/time the notification
# was sent, no matter what TIME_ZONE the project settings end up using.
PK_TZ = ZoneInfo("Asia/Karachi")


class NotificationSerializer(serializers.ModelSerializer):
    created_at = serializers.SerializerMethodField()

    # NEW: for ADMIN recipients, the name of the customer the notification
    # is about (new order / return / complaint / payment proof ...). Null
    # for customers (their own notifications need no name) and for
    # notifications that have no customer behind them (low stock, etc.).
    customer_name = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id",
            "title",
            "message",
            "type",
            "reference_type",
            "reference_id",
            "is_read",
            "created_at",
            "customer_name",
        ]
        read_only_fields = ["id", "created_at"]

    def get_customer_name(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or getattr(user, "role", None) == "customer":
            return None
        if obj.reference_type not in ("order", "return", "complaint"):
            return None

        # The list view pre-computes names for the whole page in a few
        # queries and passes them in; single-object calls fall back to a
        # one-off lookup.
        names = self.context.get("customer_names")
        if names is None:
            names = customer_names_for([obj])
        return names.get((obj.reference_type, str(obj.reference_id)))

    def get_created_at(self, obj):
        if not obj.created_at:
            return None
        # obj.created_at is stored timezone-aware (UTC) in the DB via
        # auto_now_add — convert it to Pakistan local time before sending
        # to the frontend, instead of leaving it in whatever zone the
        # active Django timezone happens to be.
        local_dt = timezone.localtime(obj.created_at, timezone=PK_TZ)
        return local_dt.isoformat()