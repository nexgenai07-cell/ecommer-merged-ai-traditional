# PATH: apps/notifications/views.py

from collections import OrderedDict

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

    FIX (E2): this view previously had no 'type' / 'is_read' filters and
    the list response was missing the mandatory 'unread_count' field.
    Both are now implemented per the v7 spec (section E2.1):
      - 'type'    -> order / promotion / system
      - 'is_read' -> true / false
      - 'unread_count' -> ALWAYS the user's total unread count across
        their ENTIRE notification history, not just the current page or
        the current filter. This is why it's computed from
        get_base_queryset() (visibility only, no type/is_read filters
        applied) rather than from get_queryset() (which has the
        filters). Computing it from the filtered/paginated queryset
        would give the wrong number whenever a filter or a page other
        than the first is active - which is exactly the bug the
        frontend team flagged.
    """

    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsPagination

    def get_base_queryset(self):
        """
        Every notification visible to the current user - their own
        (user=request.user) plus any store-wide broadcast notifications
        (user=null). No type/is_read filtering here; this is the
        baseline used both for unread_count and for retrieve/mark-read
        (so query params on other endpoints don't accidentally narrow
        what a single notification lookup can find).
        """
        return Notification.objects.filter(
            Q(user=self.request.user) | Q(user__isnull=True)
        ).order_by("-created_at")

    def get_queryset(self):
        qs = self.get_base_queryset()

        # Only the list action applies type/is_read filtering - retrieve,
        # mark_read and mark_all_read all rely on the unfiltered base
        # queryset via get_base_queryset() directly (see below), but
        # DRF's generic mixins call get_queryset() too, so we still
        # guard here in case a stray ?type=/&is_read= is present on a
        # detail request.
        if self.action != "list":
            return qs

        params = self.request.query_params

        notif_type = params.get("type")
        if notif_type:
            qs = qs.filter(type=notif_type)

        is_read = params.get("is_read")
        if is_read is not None:
            if is_read.lower() == "true":
                qs = qs.filter(is_read=True)
            elif is_read.lower() == "false":
                qs = qs.filter(is_read=False)

        return qs

    def list(self, request, *args, **kwargs):
        # unread_count = total unread across the user's ENTIRE history,
        # deliberately computed from get_base_queryset() (no type/is_read
        # filters), NOT from the filtered/paginated queryset below.
        unread_count = self.get_base_queryset().filter(is_read=False).count()

        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            paginated = self.get_paginated_response(serializer.data)
            ordered = OrderedDict()
            ordered["count"] = paginated.data["count"]
            ordered["next"] = paginated.data["next"]
            ordered["previous"] = paginated.data["previous"]
            ordered["unread_count"] = unread_count
            ordered["results"] = paginated.data["results"]
            return Response(ordered)

        serializer = self.get_serializer(queryset, many=True)
        return Response(OrderedDict([
            ("count", queryset.count()),
            ("next", None),
            ("previous", None),
            ("unread_count", unread_count),
            ("results", serializer.data),
        ]))

    @action(detail=True, methods=["put"], url_path="read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        notification.is_read = True
        notification.save()
        return Response(NotificationSerializer(notification).data)

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        """
        FIX (E2.4 - NEW endpoint, did not exist before): replaces the
        frontend firing one PUT /notifications/{id}/read/ per unread
        notification. Only ever touches the logged-in user's own
        unread notifications (get_base_queryset() is already scoped to
        this user + broadcast notifications - never another user's
        personal notifications).

        NOTE for the frontend team: broadcast notifications (user=null,
        shown to every customer) share a single is_read flag on the
        model - there's no per-user read state for them. So marking a
        broadcast notification read here marks it read for everyone,
        not just the calling user. This matches the existing behavior
        of the single mark-read endpoint (E2.3) - it isn't a new issue
        introduced by this endpoint - but flagging it since "mark all
        as read" makes it more likely to be hit. If per-user read state
        on broadcast notifications is actually needed, that requires a
        model change (a join table), which is out of scope for this
        round - let us know if you want that as a separate item.
        """
        updated_count = (
            self.get_base_queryset()
            .filter(is_read=False)
            .update(is_read=True)
        )
        return Response(
            {"marked_count": updated_count},
            status=status.HTTP_200_OK,
        )


class SendNotificationView(APIView):
    """
    POST /api/v1/notifications/send/

    Admin-only endpoint to manually create/send a notification.
    Unchanged in this round - sent_via value list (B2) is being handled
    separately.
    """

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def post(self, request):
        user_id = request.data.get("user")
        title = request.data.get("title")
        message = request.data.get("message")
        notif_type = request.data.get("type", "system")
        sent_via = request.data.get("sent_via", "web")

        if not title or not message:
            return Response(
                {"error": "title and message are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        notification = Notification.objects.create(
            store=Store.objects.first(),
            user_id=user_id,
            title=title,
            message=message,
            type=notif_type,
            sent_via=sent_via,
        )

        return Response(
            NotificationSerializer(notification).data,
            status=status.HTTP_201_CREATED,
        )