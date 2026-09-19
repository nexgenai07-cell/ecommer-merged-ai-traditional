# PATH: apps/orders/bulk_views.py   (NEW FILE, same folder as views.py)
#
# WHY THIS FILE EXISTS (Sep 2026 — Bulk Actions):
# Admin pages for Orders, Returns and Complaints had no real bulk
# endpoint, so the frontend was calling the single-item status endpoint
# once per selected row. These three views are the real bulk versions:
#
#   POST /api/v1/admin/orders/bulk-status/      (Orders  -> status)
#   POST /api/v1/admin/returns/bulk-status/     (Returns -> approve/reject)
#   POST /api/v1/admin/complaints/bulk-status/  (Complaints -> resolve/close/...)
#
# Every id is processed by calling the EXISTING single-item view
# (AdminOrderStatusUpdateView / AdminReturnStatusUpdateView /
# AdminComplaintStatusUpdateView) — no business rule is duplicated here,
# so cancel-restores-stock, forward-only order status, "approve payment
# first", return/complaint status locks, customer notifications and the
# audit log all behave exactly as they do for a single update.
#
# Ids are processed independently: if some fail (e.g. an already
# delivered order, an already-decided return), the others still go
# through, and the response lists which succeeded and which failed
# (with the reason). See core/bulk.py for the response shape.

from rest_framework import permissions
from rest_framework.views import APIView

from apps.returns.serializers import AdminReturnStatusSerializer
from apps.returns.complaint_serializers import AdminComplaintStatusSerializer
from apps.users.permissions import IsAdmin
from core.bulk import (
    parse_bulk_identifiers,
    call_single_view,
    extract_error_message,
    run_bulk,
    bulk_response,
)

from .serializers import AdminOrderStatusSerializer
from .views import AdminOrderStatusUpdateView
from .return_views import AdminReturnStatusUpdateView
from .complaint_views import AdminComplaintStatusUpdateView


class AdminOrderBulkStatusUpdateView(APIView):
    """
    POST /api/v1/admin/orders/bulk-status/

    Body:
        {
          "order_numbers": ["ORD-2026-00001", "ORD-2026-00002"],
          "status": "confirmed" | "shipped" | "out_for_delivery" |
                    "delivered" | "cancelled" | "pending_payment",
          "cancellation_reason": "..."     # required only when status = cancelled
        }

    The same status (and cancellation reason) is applied to every order.
    Each order goes through exactly the same checks as
    PUT /api/v1/admin/orders/{order_number}/status/ — orders that are not
    allowed to make that move are reported in "failed" with the reason.

    Note: tracking_number is intentionally NOT supported in bulk (one
    tracking number cannot belong to many orders) — set it per order via
    the single status endpoint.
    """

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def post(self, request):
        order_numbers = parse_bulk_identifiers(
            request.data, "order_numbers", kind="str"
        )

        # Validate the shared status / reason ONCE up front, so an
        # invalid request (bad status, cancel without a reason) is
        # rejected before any order is touched.
        payload = AdminOrderStatusSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        item_data = {"status": payload.validated_data["status"]}
        reason = payload.validated_data.get("cancellation_reason", "")
        if reason:
            item_data["cancellation_reason"] = reason

        single_view = AdminOrderStatusUpdateView()

        def process(order_number):
            ok, data = call_single_view(
                single_view.put,
                request,
                dict(item_data),
                order_number=order_number,
            )
            if not ok:
                return False, extract_error_message(data)

            detail = {
                "order_number": order_number,
                "status": data.get("status"),
            }
            if "suggested_alternatives" in data:
                detail["suggested_alternatives"] = data["suggested_alternatives"]
            return True, detail

        succeeded, failed = run_bulk(order_numbers, process)
        return bulk_response(succeeded, failed, "orders", "updated")


class AdminReturnBulkStatusUpdateView(APIView):
    """
    POST /api/v1/admin/returns/bulk-status/

    Body:
        {"ids": [12, 13, 14], "status": "approved" | "rejected"}

    Each return goes through exactly the same checks as
    PUT /api/v1/admin/returns/{id}/status/ (only a "pending" return can
    be decided; approved/rejected is final).
    """

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def post(self, request):
        ids = parse_bulk_identifiers(request.data, "ids", kind="int")

        payload = AdminReturnStatusSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        new_status = payload.validated_data["status"]

        single_view = AdminReturnStatusUpdateView()

        def process(return_id):
            ok, data = call_single_view(
                single_view.put,
                request,
                {"status": new_status},
                pk=return_id,
            )
            if not ok:
                return False, extract_error_message(data)
            return True, {"status": data.get("status", new_status)}

        succeeded, failed = run_bulk(ids, process)
        return bulk_response(succeeded, failed, "returns", new_status)


class AdminComplaintBulkStatusUpdateView(APIView):
    """
    POST /api/v1/admin/complaints/bulk-status/

    Body:
        {"ids": [5, 6, 7], "status": "open" | "in_progress" | "resolved" | "closed"}

    Each complaint goes through exactly the same status workflow as
    PUT /api/v1/admin/complaints/{id}/status/:
        open -> in_progress -> resolved (or back to open) -> closed (final)
    so e.g. bulk-resolving a complaint that is still "open" is reported
    in "failed" with the allowed next status, while the rest go through.
    """

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def post(self, request):
        ids = parse_bulk_identifiers(request.data, "ids", kind="int")

        payload = AdminComplaintStatusSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        new_status = payload.validated_data["status"]

        single_view = AdminComplaintStatusUpdateView()

        def process(complaint_id):
            ok, data = call_single_view(
                single_view.put,
                request,
                {"status": new_status},
                pk=complaint_id,
            )
            if not ok:
                return False, extract_error_message(data)
            return True, {"status": new_status}

        succeeded, failed = run_bulk(ids, process)
        return bulk_response(succeeded, failed, "complaints", "updated")