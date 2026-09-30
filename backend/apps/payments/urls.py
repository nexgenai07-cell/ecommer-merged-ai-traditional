# PATH: apps/payments/urls.py

from django.urls import path

from .views import (
    CreatePaymentIntentView,
    StripeWebhookView,
    QRProofUploadView,
    # NEW (Sep 2026 — QR 10-minute upload window)
    ExtendQRUploadTimeView,
    AdminQRPaymentPendingView,
    AdminQRPaymentApproveView,
    AdminQRPaymentRejectView,
)
# NEW: QR Payments page - stat cards + filterable list (all statuses)
from .qr_admin_views import (
    AdminQRPaymentListView,
    AdminQRPaymentStatsView,
)
# NEW: admin-uploaded payment QR image
from .qr_image_views import (
    StorePaymentQRView,
    AdminStorePaymentQRView,
)
# NEW (Sep 2026 — Bulk Actions)
from .bulk_views import (
    AdminQRPaymentBulkApproveView,
    AdminQRPaymentBulkRejectView,
)


# Mount these at /api/v1/payments/
urlpatterns = [
    path(
        "create-intent/",
        CreatePaymentIntentView.as_view(),
        name="create-payment-intent",
    ),
    path(
        "stripe/webhook/",
        StripeWebhookView.as_view(),
        name="stripe-webhook",
    ),
    # FIX (Cross-check, Sep 2026 — 404 on POST /api/v1/payments/qr/proof/):
    # QRProofUploadView was already fully implemented in views.py (proof
    # upload, duplicate-hash detection, status -> under_review, customer
    # notification) but was never wired up to a URL — this endpoint simply
    # didn't exist in urlconf, hence the 404. CreatePaymentIntentView above
    # already points QR customers at this exact path (see its "QR payments
    # do not require Stripe..." message), so no other code changes needed.
    path(
        "qr/proof/",
        QRProofUploadView.as_view(),
        name="qr-proof-upload",
    ),
    # NEW (Sep 2026 — QR 10-minute upload window): customer's "Need more
    # time?" action — one-time +5 minute extension before the order
    # auto-cancels (see cancel_stale_payments.cancel_expired_qr_placements).
    path(
        "qr/extend-time/",
        ExtendQRUploadTimeView.as_view(),
        name="qr-extend-upload-time",
    ),
    # NEW: QR image the customer scans (uploaded by admin)
    path(
        "qr/image/",
        StorePaymentQRView.as_view(),
        name="store-payment-qr-image",
    ),
]


# Mount these at /api/v1/admin/payments/
admin_payment_urlpatterns = [
    # FIX (Cross-check, Sep 2026 — 404 on GET /api/v1/admin/payments/qr/pending/):
    # same root cause as the qr/proof/ 404 above — AdminQRPaymentPendingView
    # is already fully implemented in views.py (paginated queue of
    # under_review QR payments) but was never wired up to a URL. Listed
    # before the dynamic qr/<order_number>/... patterns below purely for
    # readability (static path); it doesn't affect matching, since those
    # patterns only match paths ending in /approve/ or /reject/.
    path(
        "qr/pending/",
        AdminQRPaymentPendingView.as_view(),
        name="admin-qr-payment-pending",
    ),
    # NEW: QR Payments page. "qr/" (1 segment) and "qr/stats/" (2
    # segments) can never clash with the 3-segment
    # "qr/<order_number>/approve|reject/" patterns below, and "stats" is
    # not an order-number pattern those would match without /approve/.
    path(
        "qr/",
        AdminQRPaymentListView.as_view(),
        name="admin-qr-payment-list",
    ),
    path(
        "qr/stats/",
        AdminQRPaymentStatsView.as_view(),
        name="admin-qr-payment-stats",
    ),
    # NEW: admin uploads / replaces / removes the store's payment QR image.
    # 2 path segments, so it can't clash with qr/<order_number>/approve/.
    path(
        "qr/image/",
        AdminStorePaymentQRView.as_view(),
        name="admin-store-payment-qr-image",
    ),
    # NEW (Sep 2026 — Bulk Actions): approve / reject many QR payment
    # proofs at once. These have only 2 path segments ("qr/bulk-approve/"),
    # so they can never clash with the 3-segment
    # "qr/<order_number>/approve/" patterns below.
    path(
        "qr/bulk-approve/",
        AdminQRPaymentBulkApproveView.as_view(),
        name="admin-qr-payment-bulk-approve",
    ),
    path(
        "qr/bulk-reject/",
        AdminQRPaymentBulkRejectView.as_view(),
        name="admin-qr-payment-bulk-reject",
    ),
    path(
        "qr/<str:order_number>/approve/",
        AdminQRPaymentApproveView.as_view(),
        name="admin-qr-payment-approve",
    ),
    path(
        "qr/<str:order_number>/reject/",
        AdminQRPaymentRejectView.as_view(),
        name="admin-qr-payment-reject",
    ),
]