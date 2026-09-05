from django.db.models import Q
from rest_framework import viewsets, permissions, mixins, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsPagination
from .models import Notification
from .serializers import NotificationSerializer
from apps.users.permissions import IsAdmin
from apps.stores.models import Store


class NotificationViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET  /api/v1/notifications/                 -> list current user's notifications
    GET  /api/v1/notifications/{id}/             -> retrieve a single notification
    PUT  /api/v1/notifications/{id}/read/        -> mark one as read
    POST /api/v1/notifications/mark-all-read/    -> mark all of the user's unread notifications as read
    """

    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsPagination

    # Returns every notification visible to the logged-in user.
    # This intentionally does not apply type/is_read filters because list()
    # uses it to calculate the total unread count correctly.
    def get_base_queryset(self):
        return Notification.objects.filter(
            Q(user=self.request.user) | Q(user__isnull=True)
        ).order_by("-created_at")

    # Applies optional notification-list filters:
    # ?type=order|promotion|system
    # ?is_read=true|false
    def get_queryset(self):
        queryset = self.get_base_queryset()

        notification_type = self.request.query_params.get("type")
        if notification_type in {"order", "promotion", "system"}:
            queryset = queryset.filter(type=notification_type)

        is_read = self.request.query_params.get("is_read")
        if is_read == "true":
            queryset = queryset.filter(is_read=True)
        elif is_read == "false":
            queryset = queryset.filter(is_read=False)

        return queryset

    # Returns paginated notifications plus the user's total unread count.
    # unread_count is calculated before filters/pagination are applied.
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        unread_count = self.get_base_queryset().filter(is_read=False).count()

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            response.data["unread_count"] = unread_count
            return response

        serializer = self.get_serializer(queryset, many=True)
        return Response(
            {
                "unread_count": unread_count,
                "results": serializer.data,
            }
        )

    # Marks one notification as read.
    @action(detail=True, methods=["put"], url_path="read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        notification.is_read = True
        notification.save(update_fields=["is_read"])
        return Response(NotificationSerializer(notification).data)

    # Marks all unread notifications visible to the current user as read.
    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        updated_count = self.get_base_queryset().filter(is_read=False).update(
            is_read=True
        )
        return Response(
            {
                "message": "All notifications marked as read.",
                "updated_count": updated_count,
            }
        )


class SendNotificationView(APIView):
    """Admin-only endpoint for manually creating a notification."""

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    # Validates and creates a manually sent notification with deep-link data.
    def post(self, request):
        user_id = request.data.get("user")
        title = request.data.get("title")
        message = request.data.get("message")
        notif_type = request.data.get("type", "system")
        reference_type = request.data.get("reference_type")
        reference_id = request.data.get("reference_id")
        sent_via = request.data.get("sent_via", "web")

        if not title or not message or not reference_type or reference_id is None:
            return Response(
                {
                    "error": (
                        "title, message, reference_type and reference_id "
                        "are required."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        notification = Notification.objects.create(
            store=Store.objects.first(),
            user_id=user_id,
            title=title,
            message=message,
            type=notif_type,
            reference_type=reference_type,
            reference_id=str(reference_id),
            sent_via=sent_via,
        )

        return Response(
            NotificationSerializer(notification).data,
            status=status.HTTP_201_CREATED,
        )