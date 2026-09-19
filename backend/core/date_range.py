# PATH: core/date_range.py   (same folder as core/pagination.py)
#
# WHY THIS FILE EXISTS (Sep 2026):
# Every admin/customer list page that has a date filter used to read
# ?start_date=...&end_date=... by hand, with no validation at all:
#   - start_date later than end_date just silently returned an empty list
#   - a badly formatted date (e.g. ?start_date=abc) crashed with a 500
#
# This helper is the ONE place that validates those two query params, so
# every filter view behaves the same and the rule only has to be changed
# here:
#   - start_date may be EQUAL to end_date (a single-day filter is fine).
#   - start_date AFTER end_date  -> 400 {"error": "start_date cannot be after end_date."}
#   - a malformed / impossible date -> 400 {"error": "start_date must be a valid date in YYYY-MM-DD format."}
#   - a missing or blank param is simply ignored (same as before).
#   - past AND future dates are both allowed (this is a search filter,
#     not a creation form).
#
# IMPORTANT: this is only for READ/filter views (GET list endpoints).
# It is NOT used by Discount creation/editing — those keep their own
# separate rules (no past start_date, end must be after start) inside
# apps/products/discount_serializers.py.

from django.utils.dateparse import parse_date
from rest_framework.exceptions import ValidationError


def _parse_date_param(params, key):
    """Returns a date, or None when the param is missing/blank.
    Raises a 400 ValidationError when it is present but not a real date.

    Uses Django's own parse_date — the exact same parsing Django was
    already applying to these values inside the queryset filter — so any
    date format that worked before (e.g. 2026-09-01, 2026-9-1) keeps
    working, and only values that used to crash are now rejected cleanly.
    """
    raw = params.get(key)
    if raw is None:
        return None

    raw = raw.strip()
    if not raw:
        return None

    try:
        parsed = parse_date(raw)
    except ValueError:
        # Looks like a date but isn't a real one (e.g. 2026-02-30).
        parsed = None

    if parsed is None:
        raise ValidationError(
            {"error": f"{key} must be a valid date in YYYY-MM-DD format."}
        )

    return parsed


def get_date_range(params, start_key="start_date", end_key="end_date"):
    """Validates the date-range query params and returns (start, end).

    Each is a datetime.date or None. Raises a 400 ValidationError if a
    value is malformed or if start is after end (equal is allowed).
    """
    start = _parse_date_param(params, start_key)
    end = _parse_date_param(params, end_key)

    if start and end and start > end:
        raise ValidationError(
            {"error": f"{start_key} cannot be after {end_key}."}
        )

    return start, end


def filter_by_date_range(
    queryset,
    params,
    field="created_at",
    start_key="start_date",
    end_key="end_date",
):
    """Validates the range, then applies it to the queryset.

    Drop-in replacement for the old hand-written blocks:

        start_date = params.get("start_date")
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        end_date = params.get("end_date")
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)

    becomes:

        qs = filter_by_date_range(qs, params)
    """
    start, end = get_date_range(params, start_key, end_key)

    if start:
        queryset = queryset.filter(**{f"{field}__date__gte": start})
    if end:
        queryset = queryset.filter(**{f"{field}__date__lte": end})

    return queryset