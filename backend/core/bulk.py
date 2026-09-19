# PATH: core/bulk.py   (same folder as core/pagination.py and core/date_range.py)
#
# WHY THIS FILE EXISTS (Sep 2026):
# Six admin pages (Orders status, Products delete, Discounts delete,
# Complaints status, QR Payments approve/reject, Returns approve/reject)
# had no real bulk endpoint — the frontend was firing one request per id
# (Promise.all / Promise.allSettled). Each of those bulk endpoints now
# lives in its own app (see the *bulk_views.py files), and they all share
# the small helpers below so every one of them:
#
#   - validates the incoming id list the same way,
#   - processes each id INDEPENDENTLY (one bad id never stops the rest —
#     exactly like N separate single-endpoint calls would behave),
#   - returns the SAME response shape:
#
#         {
#           "message":       "3 of 5 orders updated.",
#           "total":         5,
#           "success_count": 3,
#           "failed_count":  2,
#           "succeeded": [ {"id": ..., ...extra info...}, ... ],
#           "failed":    [ {"id": ..., "error": "why it failed"}, ... ]
#         }
#
#     HTTP status: 200 when at least one id succeeded (frontend should
#     look at "failed" for the ones that did not), 400 when EVERY id
#     failed (so a normal error toast fires on the frontend).
#
# IMPORTANT DESIGN CHOICE — the business rules are NOT copied here.
# Each bulk view calls the existing single-item view / method for every
# id (e.g. AdminOrderStatusUpdateView.put, ProductViewSet.perform_destroy).
# So every rule that already exists for the single action (forward-only
# order status, stock release on cancel, notifications, audit log, QR
# 3-strike rule, ...) runs identically in bulk, and any future change to
# a single endpoint automatically applies to its bulk version too.

import logging

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

logger = logging.getLogger(__name__)

# Upper limit of ids accepted in ONE bulk request (protects the server
# from someone sending thousands of ids at once).
MAX_BULK_ITEMS = 100


def parse_bulk_identifiers(data, key, kind="int", max_items=MAX_BULK_ITEMS):
    """Reads and validates the list of ids from request.data[key].

    kind="int"  -> ids like [1, 2, 3]            (also accepts "1", "2")
    kind="str"  -> order numbers like ["ORD-2026-00001", ...]

    Returns a de-duplicated list (original order kept). Raises a 400
    ValidationError {"error": "..."} when the list is missing, empty,
    too long, or contains an invalid value — nothing is processed in
    that case.
    """
    if hasattr(data, "getlist"):
        # Form-encoded request (QueryDict): [1,2] arrives as repeated keys.
        raw = data.getlist(key)
    else:
        raw = data.get(key)

    if not isinstance(raw, (list, tuple)) or len(raw) == 0:
        raise ValidationError(
            {"error": f"'{key}' must be a non-empty list."}
        )

    if len(raw) > max_items:
        raise ValidationError(
            {"error": f"You can process at most {max_items} items at a time."}
        )

    cleaned = []
    seen = set()

    for value in raw:
        if kind == "int":
            # bool is a subclass of int in Python — reject it explicitly.
            if isinstance(value, bool):
                raise ValidationError(
                    {"error": f"'{key}' must contain only valid ids."}
                )
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ValidationError(
                    {"error": f"'{key}' must contain only valid ids."}
                )
        else:
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(
                    {"error": f"'{key}' must contain only non-empty strings."}
                )
            value = value.strip()

        if value not in seen:
            seen.add(value)
            cleaned.append(value)

    return cleaned


def extract_error_message(data):
    """Turns whatever an existing single-item view returned in an error
    response ({"error": "..."}, {"detail": "..."}, or a DRF field-error
    dict like {"cancellation_reason": ["..."]}) into one plain string.
    """
    if isinstance(data, dict):
        if "error" in data:
            return extract_error_message(data["error"])
        if "detail" in data:
            return extract_error_message(data["detail"])
        return " ".join(
            f"{field}: {extract_error_message(value)}"
            for field, value in data.items()
        )

    if isinstance(data, (list, tuple)):
        return " ".join(extract_error_message(item) for item in data)

    return str(data)


class ItemRequest:
    """Wraps the real request so an existing single-item view can be
    called once per id with that id's own payload.

    Only `.data` is replaced; everything else (user, META for the audit
    log's IP address, headers, ...) is passed straight through to the
    real request.
    """

    def __init__(self, request, data):
        self._request = request
        self.data = data

    def __getattr__(self, name):
        return getattr(self._request, name)


def call_single_view(handler, request, data, *args, **kwargs):
    """Calls an existing single-item handler (e.g. view.put) with a
    per-item payload and reports whether it succeeded.

    Returns (ok, response_data):
        ok             True when the handler returned a 2xx response.
        response_data  the handler's response body (dict) — on failure
                       pass it through extract_error_message().
    """
    response = handler(ItemRequest(request, data), *args, **kwargs)
    ok = 200 <= response.status_code < 300
    return ok, response.data


def run_bulk(identifiers, process_one):
    """Runs process_one(identifier) for every identifier, independently.

    process_one must return (ok, detail):
        ok=True   detail = dict of extra info to include in the success
                  entry (or None)
        ok=False  detail = error message string

    An exception in one item is caught, logged, and reported for that
    item only — the remaining items are still processed.

    Returns (succeeded, failed) lists ready for bulk_response().
    """
    succeeded = []
    failed = []

    for identifier in identifiers:
        try:
            ok, detail = process_one(identifier)
        except ValidationError as exc:
            ok, detail = False, extract_error_message(exc.detail)
        except Exception:
            logger.exception("Bulk action failed for item %r", identifier)
            ok, detail = False, "Something went wrong while processing this item."

        if ok:
            entry = {"id": identifier}
            if detail:
                entry.update(detail)
            succeeded.append(entry)
        else:
            failed.append({"id": identifier, "error": detail})

    return succeeded, failed


def bulk_response(succeeded, failed, noun, verb):
    """Builds the common bulk response.

    noun  e.g. "orders"     verb  e.g. "updated" / "deleted" / "approved"
    """
    total = len(succeeded) + len(failed)

    body = {
        "message": f"{len(succeeded)} of {total} {noun} {verb}.",
        "total": total,
        "success_count": len(succeeded),
        "failed_count": len(failed),
        "succeeded": succeeded,
        "failed": failed,
    }

    http_status = (
        status.HTTP_200_OK if succeeded else status.HTTP_400_BAD_REQUEST
    )

    return Response(body, status=http_status)