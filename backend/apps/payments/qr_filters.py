# PATH: apps/payments/qr_filters.py
#
# ONE shared place for "which QR payments does the admin want to see".
# Used by BOTH the QR Payments page list (qr_admin_views.py) and the CSV
# export (analytics/dashboard_views.py, ?type=qr_payments), so what you
# export is always exactly what is filtered on screen - the same rule the
# other admin pages follow.
#
# Only QR payments whose customer actually SUBMITTED a proof are listed
# (qr_submitted_at is set). Orders that are still waiting for the customer
# to upload a proof have nothing for the admin to review, so they are not
# rows (they are counted separately as "awaiting_proof" in the stats).

from decimal import Decimal, InvalidOperation

from django.db.models import Q
from rest_framework.exceptions import ValidationError

from apps.orders.models import Payment

# status values the page can send -> real Payment.status value.
# "approved" / "pending" are accepted aliases (what admins call them).
# "all" (or nothing / anything unknown) = no status filter.
STATUS_ALIASES = {
    "under_review": "under_review",
    "pending": "under_review",
    "pending_review": "under_review",
    "paid": "paid",
    "approved": "paid",
    "rejected": "rejected",
    "refunded": "refunded",
}

# ordering param -> real ordering. Fixed whitelist so no arbitrary column
# name can be passed in. Default is newest submission first.
ORDERING_MAP = {
    "submitted_at": "qr_submitted_at",
    "-submitted_at": "-qr_submitted_at",
    "amount": "order__total_amount",
    "-amount": "-order__total_amount",
    "customer_name": "order__customer__name",
    "-customer_name": "-order__customer__name",
}
DEFAULT_ORDERING = "-qr_submitted_at"


def base_qr_queryset():
    """Every QR payment that has a submitted proof, with related rows
    loaded so a page of results needs no extra queries."""
    return (
        Payment.objects.filter(
            payment_method="qr",
            qr_submitted_at__isnull=False,
        )
        .select_related("order", "order__customer", "order__customer__user")
    )


def _parse_amount(params, key):
    raw = (params.get(key) or "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValidationError({key: "Must be a valid number."})
    if not value.is_finite() or value < 0:
        raise ValidationError({key: "Must be a valid non-negative number."})
    return value


def _parse_bool(params, key):
    raw = (params.get(key) or "").strip().lower()
    if raw in ("true", "1", "yes"):
        return True
    if raw in ("false", "0", "no"):
        return False
    return None


def apply_qr_filters(qs, params, start_date=None, end_date=None):
    """
    params: request.query_params
    start_date / end_date: already-validated date objects (or None),
    compared against the day the proof was submitted.

    Supported params:
      status      under_review | paid | rejected | refunded | all
                  (aliases: pending, approved)
      search      order number, customer name / phone / email,
                  transaction id
      min_amount / max_amount   on the order total
      duplicate   true | false   (duplicate-proof warning flag)
      ordering    submitted_at | amount | customer_name (prefix - = desc)
    """
    # 1. status
    status_param = (params.get("status") or "").strip().lower()
    real_status = STATUS_ALIASES.get(status_param)
    if real_status:
        qs = qs.filter(status=real_status)

    # 2. search
    search = (params.get("search") or "").strip()
    if search:
        qs = qs.filter(
            Q(order__order_number__icontains=search)
            | Q(order__customer__name__icontains=search)
            | Q(order__customer__phone__icontains=search)
            | Q(order__customer__email__icontains=search)
            | Q(qr_transaction_id__icontains=search)
        )

    # 3. date range (day the proof was submitted)
    if start_date:
        qs = qs.filter(qr_submitted_at__date__gte=start_date)
    if end_date:
        qs = qs.filter(qr_submitted_at__date__lte=end_date)

    # 4. amount range
    min_amount = _parse_amount(params, "min_amount")
    max_amount = _parse_amount(params, "max_amount")
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        raise ValidationError({"min_amount": "min_amount cannot be greater than max_amount."})
    if min_amount is not None:
        qs = qs.filter(order__total_amount__gte=min_amount)
    if max_amount is not None:
        qs = qs.filter(order__total_amount__lte=max_amount)

    # 5. duplicate-proof warning
    duplicate = _parse_bool(params, "duplicate")
    if duplicate is not None:
        qs = qs.filter(qr_duplicate_warning=duplicate)

    # 6. ordering (whitelist; "-id" keeps equal values in a stable order)
    ordering = ORDERING_MAP.get(params.get("ordering"), DEFAULT_ORDERING)
    return qs.order_by(ordering, "-id")