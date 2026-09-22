# PATH: apps/notifications/views.py

from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import viewsets, permissions, mixins, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsPagination
from .models import Notification
from .serializers import NotificationSerializer
from .utils import create_notification
from apps.users.permissions import IsAdmin


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
    #
    # FIX (Sep 2026 — new customers were seeing old notifications): the
    # user__isnull=True branch matches broadcast notifications, which are
    # visible to every customer of the store (see SendNotificationView).
    # It previously matched ALL broadcast notifications regardless of when
    # they were created, so a brand-new customer immediately saw every
    # promotion/system broadcast ever sent before they even registered.
    # Notifications targeted at this specific user (user=self.request.user)
    # don't have this problem — they can only exist for an already-
    # registered user — so only the broadcast branch needs the extra
    # created_at >= registration-date filter.
    def get_base_queryset(self):
        return Notification.objects.filter(
            Q(user=self.request.user)
            | Q(user__isnull=True, created_at__gte=self.request.user.created_at)
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
    #
    # NEW (Frontend audit, Sep 2026): added `unread_by_type` — a count
    # per notification type (order/promotion/system), computed the same
    # unfiltered way as unread_count. Previously only the "Unread" tab
    # could show a real count badge; Orders/Promotions/System tabs had
    # no count at all since the backend only ever returned one grand
    # total. Additive only — unread_count and results are unchanged.
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        base_unread = self.get_base_queryset().filter(is_read=False)
        unread_count = base_unread.count()
        unread_by_type = {
            value: base_unread.filter(type=value).count()
            for value, _label in Notification.TYPE_CHOICES
        }

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            response.data["unread_count"] = unread_count
            response.data["unread_by_type"] = unread_by_type
            return response

        serializer = self.get_serializer(queryset, many=True)
        return Response(
            {
                "unread_count": unread_count,
                "unread_by_type": unread_by_type,
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
    """
    POST /api/v1/notifications/send/

    Admin-only endpoint for manually creating a notification — either to
    one specific user (pass "user": <user_id>), or a broadcast visible to
    every logged-in user (omit "user", or pass null — NotificationViewSet.
    get_base_queryset() already shows user=null notifications to everyone).

    FIX (Sep 2026 — "Send Notification" button not working for a specific
    user): reference_type and reference_id were being treated as
    REQUIRED here ("... are required", 400) even though the model
    defines both as null=True/blank=True — genuinely optional by design.
    The frontend's Send Notification form only ever collects
    user/title/message/type/channel — it has no order/return/complaint/
    product to reference, since this is a free-form admin message, not
    tied to any record. So EVERY request from that form was failing this
    check with 400, which is exactly why the button appeared to do
    nothing (this affected both the specific-user case and the broadcast
    case equally, since the check ran regardless of whether "user" was
    provided).

    Also switched from calling Notification.objects.create() directly to
    the shared create_notification() helper (utils.py), so a manually
    sent notification gets the same "no Store configured yet -> log and
    return None instead of crashing" safety net every other notification
    path in the app already has, instead of raising an unhandled error.

    reference_type/reference_id are each optional independently (both
    omitted = a general/broadcast notification), but are validated as
    required-together: if exactly one is given without the other, this
    now returns 400 instead of silently saving a half-filled reference.
    """

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    # Validates and creates a manually sent notification.
    def post(self, request):
        user_id = request.data.get("user")
        title = request.data.get("title")
        message = request.data.get("message")
        notif_type = request.data.get("type", "system")
        reference_type = request.data.get("reference_type")
        reference_id = request.data.get("reference_id")
        sent_via = request.data.get("sent_via", "web")

        if not title or not message:
            return Response(
                {"error": "title and message are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # NEW (Sep 2026 — Send Notification, broadcast fix follow-up):
        # reference_type/reference_id are each optional on their own (a
        # general/broadcast notification has neither), but if one is
        # given the other must be too — a half-filled reference would
        # save a dangling pointer that breaks deep-linking (clicking the
        # notification to open the referenced order/return/complaint)
        # later, with no error raised at the time it happened.
        if bool(reference_type) != bool(reference_id):
            return Response(
                {
                    "error": (
                        "reference_type and reference_id must be provided "
                        "together, or both left out for a general "
                        "notification."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if notif_type not in dict(Notification.TYPE_CHOICES):
            return Response(
                {
                    "error": f"Invalid type '{notif_type}'.",
                    "accepted_values": list(dict(Notification.TYPE_CHOICES)),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if sent_via not in dict(Notification.SENT_VIA_CHOICES):
            return Response(
                {
                    "error": f"Invalid sent_via '{sent_via}'.",
                    "accepted_values": list(dict(Notification.SENT_VIA_CHOICES)),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # user is optional (null = broadcast). If given, it must resolve
        # to a real user — resolved to an actual User instance here
        # because create_notification()/Notification.user is a
        # ForeignKey and can't be assigned a raw id directly.
        target_user = None
        if user_id is not None:
            target_user = get_user_model().objects.filter(id=user_id).first()
            if target_user is None:
                return Response(
                    {"error": f"No user found with id {user_id}."},
                    status=status.HTTP_404_NOT_FOUND,
                )

        notification = create_notification(
            user=target_user,
            title=title,
            message=message,
            notification_type=notif_type,
            sent_via=sent_via,
            reference_type=reference_type,
            reference_id=reference_id,
        )

        if notification is None:
            return Response(
                {
                    "error": (
                        "Could not create notification — no store is "
                        "configured on this platform yet."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            NotificationSerializer(notification).data,
            status=status.HTTP_201_CREATED,
        )