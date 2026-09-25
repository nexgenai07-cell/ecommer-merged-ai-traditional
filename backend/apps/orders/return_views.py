# PATH: apps/orders/return_views.py

import re
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.utils import timezone
from rest_framework import status, permissions, generics
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.returns.models import Return
from apps.returns.serializers import (
    ReturnSerializer,
    CreateReturnSerializer,
    AdminReturnStatusSerializer,
)
from apps.notifications.utils import create_notification, notify_store_admins
from apps.ai.audit import log_manual_admin_action as log_admin_action
from .models import Order, OrderStatusHistory
from apps.users.permissions import IsAdmin
from core.pagination import StandardResultsPagination
from core.date_range import filter_by_date_range


# NEW (Sep 2026 — Returns/Complaints ID search bug report): the admin table
# shows a return's ID as "#RET-20" and a complaint's as "#CMP-51" (the
# frontend builds that label from the numeric id), but search only
# recognised the exact spelling "RET-20" / "CMP-51". Copying what's on
# screen ("#RET-20"), or typing "RET 20", "ret_20", "#20" or just "20" found
# nothing. Shared by the returns list, the complaints list and both CSV
# exports (apps/analytics/dashboard_views.py) so they all understand the
# same spellings.
_REFERENCE_RE = re.compile(
    r"^#?\s*(?:(?P<prefix>[a-z]+)\s*[-_ ]?\s*)?(?P<number>\d{1,9})$",
    re.IGNORECASE,
)


def reference_id_from_search(search, prefix):
    """
    Reads a record ID out of a search box value.

    Accepted spellings for prefix="ret" (any case): "#RET-20", "RET-20",
    "ret20", "RET 20", "ret_20", "#20", "20".

    Returns None when the text isn't an ID (or carries a DIFFERENT prefix,
    e.g. "CMP-5" while searching returns) — the caller then just does its
    normal text search.
    Otherwise returns (id, explicit):
      explicit=True  -> the text has a "#" or the prefix ("#RET-20",
                        "RET-20", "#20"): it can only mean that ID, so the
                        caller should match ONLY that record.
      explicit=False -> a bare number ("20"): could equally be part of an
                        order number or a name, so the caller should match
                        the ID OR the normal text fields.
    The number is capped at 9 digits so a huge value can never overflow
    the database's integer column.
    """
    text = (search or "").strip()
    match = _REFERENCE_RE.match(text)
    if not match:
        return None

    given_prefix = match.group("prefix")
    if given_prefix and given_prefix.lower() != prefix.lower():
        return None

    explicit = bool(given_prefix) or text.startswith("#")
    return int(match.group("number")), explicit


class CreateReturnView(APIView):
    """
    POST /api/v1/orders/{order_number}/return/
    Creates a customer return request for a delivered order.
    """

    permission_classes = [permissions.IsAuthenticated]

    # Validates ownership and creates one return request for the order.
    def post(self, request, order_number):
        try:
            order = Order.objects.get(
                order_number=order_number,
                customer__user=request.user,
            )
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if order.status != "delivered":
            return Response(
                {"error": "Only delivered orders are eligible for return."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if Return.objects.filter(
            order=order,
            status__in=["pending", "approved"],
        ).exists():
            return Response(
                {"error": "A return request already exists for this order."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = CreateReturnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return_request = Return.objects.create(
            order=order,
            customer=order.customer,
            reason=serializer.validated_data["reason"],
            status="pending",
        )

        # NEW (Notification Triggers Addendum, Item 15): "New return
        # request" — every admin of the store must be notified.
        # UPDATED (v4.0): store.owner (single FK) removed — a store can
        # now have multiple admins (Store.admins), so this now goes to
        # all of them via notify_store_admins() instead of a single
        # create_notification() call to store.owner.
        notify_store_admins(
            order.store,
            title="New return request",
            message=(
                f"A return request has been submitted for order "
                f"{order.order_number}."
            ),
            notification_type="order",
            reference_type="return",
            reference_id=return_request.id,
        )

        return Response(
            ReturnSerializer(return_request).data,
            status=status.HTTP_201_CREATED,
        )


class ReturnListView(generics.ListAPIView):
    """GET /api/v1/returns/ — lists returns visible to the user.

    FIX (Frontend Bug Report — Returns list, Sep 2026): none of
    status/search/start_date/end_date/ordering were ever read from
    query_params — this always just returned every visible return
    ordered by -created_at, which is why every request (regardless of
    filters) returned the same records and the stat cards were all
    identical. All five params are now applied server-side.
    """

    serializer_class = ReturnSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsPagination

    # Fields the "ordering" param is allowed to map to — a fixed
    # whitelist so no arbitrary/unsafe column name can be passed in.
    ORDERING_MAP = {
        "created_at": "created_at",
        "-created_at": "-created_at",
        "customer_name": "customer__name",
        "-customer_name": "-customer__name",
    }

    # Admins see all returns; customers see only their own.
    def get_queryset(self):
        if self.request.user.role == "admin":
            qs = Return.objects.all()
        else:
            qs = Return.objects.filter(customer__user=self.request.user)

        qs = qs.select_related("order", "customer")

        params = self.request.query_params

        # 1. status — accepted values: pending, approved, rejected
        # (matches Return.STATUS_CHOICES; anything else is ignored
        # rather than erroring). UPDATED (Sep 2026): "requested" is now
        # "pending"; the old "requested" value is still accepted as an
        # alias so an older frontend build keeps working.
        status_param = params.get("status")
        if status_param == "requested":
            status_param = "pending"
        if status_param in ("pending", "approved", "rejected"):
            qs = qs.filter(status=status_param)

        # 2. search — order number, reason text, customer name, and the
        # return's own reference number (e.g. "RET-16" -> id=16).
        search = params.get("search", "").strip()
        if search:
            search_filter = (
                Q(order__order_number__icontains=search)
                | Q(reason__icontains=search)
                | Q(customer__name__icontains=search)
            )

            # FIX (Sep 2026 — ID search bug report): "#RET-20", "RET 20",
            # "#20" etc. now work too, not just "RET-20" — see
            # reference_id_from_search(). A prefixed/"#" ID matches only
            # that return; a bare number also matches the text fields.
            ref = reference_id_from_search(search, "ret")
            if ref:
                ref_id, explicit = ref
                if explicit:
                    search_filter = Q(id=ref_id)
                else:
                    search_filter |= Q(id=ref_id)

            qs = qs.filter(search_filter)

        # 3. start_date / end_date (YYYY-MM-DD) against created_at.
        # UPDATED (Sep 2026): validated in one shared place (start_date
        # may equal end_date but not be after it; bad dates give a 400).
        qs = filter_by_date_range(qs, params)

        # 4. ordering — whitelisted values only; default stays
        # -created_at when the param is missing/invalid.
        ordering = self.ORDERING_MAP.get(params.get("ordering"), "-created_at")
        qs = qs.order_by(ordering)

        return qs


class ReturnDetailView(generics.RetrieveAPIView):
    """GET /api/v1/returns/{id}/"""

    serializer_class = ReturnSerializer
    permission_classes = [permissions.IsAuthenticated]

    # Restricts customer detail access to their own return requests.
    def get_queryset(self):
        if self.request.user.role == "admin":
            return Return.objects.all()

        return Return.objects.filter(customer__user=self.request.user)


class AdminReturnStatusUpdateView(APIView):
    """PUT /api/v1/admin/returns/{id}/status/"""

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    # Updates a return to approved/rejected and creates the exact
    # customer notification required for that decision.
    # UPDATED (Sep 2026): only a "pending" return can be updated; once it
    # is approved or rejected the decision is final.
    def put(self, request, pk):
        try:
            return_request = Return.objects.get(id=pk)
        except Return.DoesNotExist:
            return Response(
                {"error": "Return request not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not return_request.can_update_status:
            return Response(
                {
                    "error": (
                        f"This return has already been "
                        f"{return_request.status} and its status cannot "
                        "be changed."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = AdminReturnStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        old_status = return_request.status
        return_request.status = serializer.validated_data["status"]
        return_request.resolved_at = timezone.now()
        try:
            return_request.save()
        except DjangoValidationError as exc:
            # Safety net if the model's own status lock rejects the change.
            return Response(
                {"error": "; ".join(exc.messages)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        notification_text = {
            "approved": (
                "Return approved",
                (
                    f"Your return request for order "
                    f"{return_request.order.order_number} has been approved."
                ),
            ),
            "rejected": (
                "Return rejected",
                (
                    f"Your return request for order "
                    f"{return_request.order.order_number} has been rejected."
                ),
            ),
        }

        title, message = notification_text[return_request.status]

        # NEW (Sep 2026 — return step missing from Order Timeline): same
        # gap as the refund step (see OrderCancelView / 
        # AdminOrderStatusUpdateView) — an approved return changed
        # Return.status and fired a notification, but nothing ever
        # pushed a matching entry into the order's status_history, so
        # the Order Timeline never showed that the order was returned.
        # Recorded here, with the exact date/time it was approved, just
        # like every other timeline entry. A rejected return isn't a
        # change to the order itself, so it isn't logged here.
        if return_request.status == "approved":
            OrderStatusHistory.record(
                return_request.order,
                "returned",
                note=f"Return approved: {return_request.reason}",
            )

        create_notification(
            user=return_request.customer.user,
            store=return_request.order.store,
            title=title,
            message=message,
            notification_type="order",
            reference_type="return",
            reference_id=return_request.id,
        )

        # FIX (Frontend Bug Report — Audit Logs, Sep 2026): no admin write
        # endpoint besides Adjust Stock was writing to the shared AuditLog
        # table (API 82 / System Activity Logs). Logged here now.
        log_admin_action(
            store=return_request.order.store,
            user=request.user,
            action="update_return_status",
            entity="return",
            entity_id=return_request.id,
            old_data={"status": old_status},
            new_data={"status": return_request.status},
            request=request,
        )

        return Response(
            {
                "message": "Return status updated.",
                "status": return_request.status,
            }
        )