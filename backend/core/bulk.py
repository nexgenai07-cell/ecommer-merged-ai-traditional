# PATH: core/bulk.py   (same folder as core/pagination.py and core/date_range.py)
#
# WHY THIS FILE EXISTS (Sep 2026):
# Six admin pages (Orders status, Products delete, Discounts delete,
# Complaints status, QR Payments approve/reject, Returns approve/reject)
# had no real bulk endpoint — the frontend was firing one request per id
# (Promise.all / Promise.allSettled). Each of those bulk endpoints now
# lives in its own app (see the *bulk_views.py files), and they all share
# the small helpers below.
#
# RESPONSE CONTRACT — follows Bulk Delete Categories (API 27.2), which is
# the bulk shape the frontend already integrates and asked to keep:
#
#   POST  { "ids": [1, 2, 3] }            (orders / QR use "order_numbers")
#
#   200 OK  (always, once the request itself is valid — a bad id never
#            fails the whole batch)
#   {
#     "<verb>_ids":  [1, 2],     # exactly what was actually processed
#                                # (deleted_ids / updated_ids / approved_ids /
#                                #  rejected_ids — same idea as deleted_ids
#                                #  in API 27.2)
#     "missing_ids": [3],        # ids that don't exist (or are already
#                                # deleted) — quietly reconcile these
#     "failed": [                # status endpoints only: ids that exist but
#       {"id": 2, "error": "..."}#  the normal business rules refused (e.g.
#     ],                         #  "already delivered") — same error text
#                                #  the single endpoint would have returned
#     "message": "2 order(s) updated successfully.",
#     "results": [ ... ]         # status endpoints only: per-id extra info
#   }
#
#   400 Bad Request — the request itself is invalid (missing / empty /
#   too long / malformed id list):  { "detail": "ids must be a non-empty list of ... ids." }
#   (same {"detail": ...} shape as API 27.2). Invalid shared fields such as
#   an unknown "status", a cancel without a reason, or a QR reject without
#   a reason return the same field-keyed 400 the single endpoint gives.
#
# IMPORTANT DESIGN CHOICE — the business rules are NOT copied here.
# Each bulk view calls the existing single-item view / method for every
# id (e.g. AdminOrderStatusUpdateView.put, ProductViewSet.perform_destroy).
# So every rule that already exists for the single action (forward-only
# order status, stock release on cancel, notifications, audit log, QR
# 3-strike rule, ...) runs identically in bulk, and any future change to
# a single endpoint automatically applies to its bulk version too. Each id
# is processed independently, exactly like N separate single calls.

import logging

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

logger = logging.getLogger(__name__)

# Upper limit of ids accepted in ONE bulk request (protects the server
# from someone sending thousands of ids at once).
MAX_BULK_ITEMS = 100

# What process_one() returns as its first value when the id does not exist
# (goes into "missing_ids"), instead of True (done) / False (rule refused).
MISSING = "missing"


def _bad_list(message):
    return ValidationError({"detail": message})


def parse_bulk_identifiers(data, key, kind="int", label="ids", max_items=MAX_BULK_ITEMS):
    """Reads and validates the list of ids from request.data[key].

    kind="int"  -> ids like [1, 2, 3]            (also accepts "1", "2")
    kind="str"  -> order numbers like ["ORD-2026-00001", ...]
    label       -> used in the error text, e.g. "product ids"

    Returns a de-duplicated list (original order kept). Raises a 400
    ValidationError {"detail": "..."} (same shape as API 27.2) when the
    list is missing, empty, too long, or contains an invalid value —
    nothing is processed in that case.
    """
    if hasattr(data, "getlist"):
        # Form-encoded request (QueryDict): [1,2] arrives as repeated keys.
        raw = data.getlist(key)
    else:
        raw = data.get(key)

    if not isinstance(raw, (list, tuple)) or len(raw) == 0:
        raise _bad_list(f"{key} must be a non-empty list of {label}.")

    if len(raw) > max_items:
        raise _bad_list(f"You can process at most {max_items} items at a time.")

    cleaned = []
    seen = set()

    for value in raw:
        if kind == "int":
            # bool is a subclass of int in Python — reject it explicitly.
            if isinstance(value, bool):
                raise _bad_list(f"{key} must contain only valid {label}.")
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise _bad_list(f"{key} must contain only valid {label}.")
        else:
            if not isinstance(value, str) or not value.strip():
                raise _bad_list(f"{key} must contain only valid {label}.")
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
    per-item payload.

    Returns (outcome, response_data):
        outcome  True     -> the handler returned a 2xx response
                 MISSING  -> the handler returned 404 (id doesn't exist)
                 False    -> any other error (a business rule refused it)
        response_data  the handler's response body (dict) — on False pass
                       it through extract_error_message().
    """
    response = handler(ItemRequest(request, data), *args, **kwargs)

    if 200 <= response.status_code < 300:
        return True, response.data
    if response.status_code == status.HTTP_404_NOT_FOUND:
        return MISSING, response.data
    return False, response.data


def run_bulk(identifiers, process_one):
    """Runs process_one(identifier) for every identifier, independently.

    process_one must return (outcome, detail):
        (True, dict_or_None)   done — dict = extra info for "results"
        (False, "message")     exists, but the rules refused it -> "failed"
        (MISSING, None)        doesn't exist                    -> "missing_ids"

    An exception in one item is caught, logged, and reported as "failed"
    for that item only — the remaining items are still processed.

    Returns (done, missing_ids, failed) ready for bulk_response().
    """
    done = []
    missing_ids = []
    failed = []

    for identifier in identifiers:
        try:
            outcome, detail = process_one(identifier)
        except ValidationError as exc:
            outcome, detail = False, extract_error_message(exc.detail)
        except Exception:
            logger.exception("Bulk action failed for item %r", identifier)
            outcome, detail = False, "Something went wrong while processing this item."

        if outcome is True:
            entry = {"id": identifier}
            if detail:
                entry.update(detail)
            done.append(entry)
        elif outcome == MISSING:
            missing_ids.append(identifier)
        else:
            failed.append({"id": identifier, "error": detail})

    return done, missing_ids, failed


def bulk_response(done, missing_ids, failed, ids_key, noun, verb, include_details=True):
    """Builds the common bulk response (see the contract at the top).

    ids_key          e.g. "deleted_ids" / "updated_ids" / "approved_ids"
    noun, verb       e.g. "order(s)", "updated"
    include_details  True  -> also send "failed" and "results" (status
                              endpoints, where business rules can refuse an id)
                     False -> exact Bulk Delete Categories shape:
                              {<ids_key>, missing_ids, message} ("failed" only
                              appears if something unexpected went wrong)
    """
    message = f"{len(done)} {noun} {verb} successfully."
    not_done = len(missing_ids) + len(failed)
    # The delete endpoints keep the exact API 27.2 message (successes only,
    # missing ids are already listed in "missing_ids").
    if not_done and (include_details or failed):
        message += f" {not_done} could not be {verb}."

    body = {
        ids_key: [entry["id"] for entry in done],
        "missing_ids": missing_ids,
    }

    # "failed" is always sent when something actually failed, even for the
    # delete endpoints — otherwise an unexpected error on one id would be
    # silently invisible (it would just be absent from <ids_key>).
    if include_details or failed:
        body["failed"] = failed

    body["message"] = message

    if include_details:
        body["results"] = done

    return Response(body, status=status.HTTP_200_OK)