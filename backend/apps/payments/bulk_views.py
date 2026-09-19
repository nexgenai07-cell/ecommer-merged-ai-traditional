# PATH: apps/payments/bulk_views.py   (NEW FILE, same folder as views.py)
#
# WHY THIS FILE EXISTS (Sep 2026 — Bulk Actions):
# The QR Payments admin page had no bulk endpoint, so the frontend was
# calling approve / reject once per selected row. These are the real
# bulk versions:
#
#   POST /api/v1/admin/payments/qr/bulk-approve/
#   POST /api/v1/admin/payments/qr/bulk-reject/
#
# Every order is processed by calling the EXISTING single-item view
# (AdminQRPaymentApproveView / AdminQRPaymentRejectView), so all of its
# rules run unchanged for each one: QR-only, payment must be
# "under_review", approve -> payment paid + order confirmed + stock
# confirmed, reject -> the 3-strike rule (3rd rejection cancels the order
# and releases stock), customer notification and audit log per order.
#
# Orders are independent: one that cannot be approved/rejected (e.g. its
# payment is no longer under_review) is reported in "failed" with the
# reason while the rest still go through. See core/bulk.py for the
# response shape.

from rest_framework import permissions
from rest_framework.views import APIView

from apps.users.permissions import IsAdmin
from core.bulk import (
    parse_bulk_identifiers,
    call_single_view,
    extract_error_message,
    run_bulk,
    bulk_response,
)

from .serializers import QRRejectSerializer
from .views import AdminQRPaymentApproveView, AdminQRPaymentRejectView


class AdminQRPaymentBulkApproveView(APIView):
    """
    POST /api/v1/admin/payments/qr/bulk-approve/

    Body: {"order_numbers": ["ORD-2026-00001", "ORD-2026-00002"]}
    """

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def post(self, request):
        order_numbers = parse_bulk_identifiers(
            request.data, "order_numbers", kind="str"
        )

        single_view = AdminQRPaymentApproveView()

        def process(order_number):
            ok, data = call_single_view(
                single_view.put,
                request,
                {},
                order_number=order_number,
            )
            if not ok:
                return False, extract_error_message(data)

            return True, {
                "order_number": order_number,
                "payment_status": data.get("payment_status"),
                "order_status": data.get("order_status"),
            }

        succeeded, failed = run_bulk(order_numbers, process)
        return bulk_response(succeeded, failed, "QR payments", "approved")


class AdminQRPaymentBulkRejectView(APIView):
    """
    POST /api/v1/admin/payments/qr/bulk-reject/

    Body: {"order_numbers": ["ORD-2026-00001", ...], "reason": "string"}

    The same reason (mandatory, exactly like the single reject endpoint)
    is applied to every order and shown to each customer.
    """

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def post(self, request):
        order_numbers = parse_bulk_identifiers(
            request.data, "order_numbers", kind="str"
        )

        # reason is mandatory — validated once up front so nothing is
        # rejected when it is missing.
        payload = QRRejectSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        reason = payload.validated_data["reason"]

        single_view = AdminQRPaymentRejectView()

        def process(order_number):
            ok, data = call_single_view(
                single_view.put,
                request,
                {"reason": reason},
                order_number=order_number,
            )
            if not ok:
                return False, extract_error_message(data)

            return True, {
                "order_number": order_number,
                "payment_status": data.get("payment_status"),
                "order_status": data.get("order_status"),
                "rejection_count": data.get("rejection_count"),
                "permanently_cancelled": data.get("permanently_cancelled"),
            }

        succeeded, failed = run_bulk(order_numbers, process)
        return bulk_response(succeeded, failed, "QR payments", "rejected")