# PATH: apps/payments/qr_admin_views.py
#
# Admin "QR Payment Verification" page: stat cards + filterable list.
# (Export lives in the shared CSV export: GET /api/v1/analytics/export/
# ?type=qr_payments, which uses the very same filters - see qr_filters.py.)
#
# The old GET .../qr/pending/ (review queue: under_review only) is left
# exactly as it was; these are two NEW endpoints next to it.

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.orders.models import Payment
from apps.users.permissions import IsAdmin
from core.date_range import get_date_range
from core.pagination import StandardResultsPagination

from .qr_filters import apply_qr_filters, base_qr_queryset


class AdminQRPaymentListView(APIView):
    """
    GET /api/v1/admin/payments/qr/

    Every QR payment that has a submitted proof, in ANY status, filtered
    server-side. Paginated, standard shape {count, next, previous, results}.

    Query params (all optional):
      status        under_review | paid | rejected | refunded | all
      search        order number, customer name/phone/email, transaction id
      start_date / end_date   (YYYY-MM-DD, day the proof was submitted)
      min_amount / max_amount
      duplicate     true | false
      ordering      submitted_at | amount | customer_name  (- prefix = desc)
      page / page_size   (same pagination as every other admin list)
    """
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get(self, request):
        start_date, end_date = get_date_range(request.query_params)

        payments = apply_qr_filters(
            base_qr_queryset(), request.query_params, start_date, end_date
        )

        paginator = StandardResultsPagination()
        page = paginator.paginate_queryset(payments, request, view=self)

        results = []
        for payment in page:
            order = payment.order
            customer = order.customer
            results.append({
                "id": payment.id,
                "order_number": order.order_number,
                "customer": {
                    "id": customer.id,
                    "name": customer.name,
                    "phone": customer.phone,
                },
                "amount": str(order.total_amount),
                "status": payment.status,
                "screenshot_url": payment.qr_screenshot_url,
                "transaction_id": payment.qr_transaction_id or "",
                "submitted_at": (
                    payment.qr_submitted_at.isoformat()
                    if payment.qr_submitted_at else None
                ),
                "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
                "duplicate_warning": payment.qr_duplicate_warning,
                "rejection_count": payment.qr_rejection_count,
                "reject_reason": payment.qr_reject_reason or "",
                "order_status": order.status,
            })

        return paginator.get_paginated_response(results)


class AdminQRPaymentStatsView(APIView):
    """
    GET /api/v1/admin/payments/qr/stats/

    Numbers for the stat cards on the QR Payments page. Optional
    start_date / end_date narrow them to proofs submitted in that period;
    status / search do NOT change them (the cards always show the overall
    picture, like the Products page cards).

    Response:
      total               QR payments with a submitted proof
      pending_review      waiting for the admin to approve / reject
      approved            approved (paid)
      rejected            currently rejected
      refunded            approved earlier, refunded since
      duplicate_warnings  proofs flagged as a possible duplicate
      submitted_today     proofs submitted today
      awaiting_proof      QR orders still waiting for the customer's proof
      approved_amount     sum of order totals of approved payments
      pending_amount      sum of order totals waiting for review
    """
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get(self, request):
        start_date, end_date = get_date_range(request.query_params)

        qs = base_qr_queryset()
        if start_date:
            qs = qs.filter(qr_submitted_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(qr_submitted_at__date__lte=end_date)

        zero = Value(0, output_field=DecimalField(max_digits=14, decimal_places=2))
        today = timezone.localdate()

        agg = qs.aggregate(
            total=Count("id"),
            pending_review=Count("id", filter=Q(status="under_review")),
            approved=Count("id", filter=Q(status="paid")),
            rejected=Count("id", filter=Q(status="rejected")),
            refunded=Count("id", filter=Q(status="refunded")),
            duplicate_warnings=Count("id", filter=Q(qr_duplicate_warning=True)),
            submitted_today=Count("id", filter=Q(qr_submitted_at__date=today)),
            approved_amount=Coalesce(
                Sum("order__total_amount", filter=Q(status="paid")), zero
            ),
            pending_amount=Coalesce(
                Sum("order__total_amount", filter=Q(status="under_review")), zero
            ),
        )

        # Not a date-range number: how many QR orders are right now still
        # waiting for the customer to upload a proof.
        agg["awaiting_proof"] = Payment.objects.filter(
            payment_method="qr",
            qr_submitted_at__isnull=True,
            order__status="order_placed",
        ).count()

        agg["approved_amount"] = str(agg["approved_amount"])
        agg["pending_amount"] = str(agg["pending_amount"])

        return Response(agg)