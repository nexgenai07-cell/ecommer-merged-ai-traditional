from django.urls import path

from .views import (
    CreatePaymentIntentView,
    StripeWebhookView,
    QRProofUploadView,
    AdminQRPaymentPendingView,
    AdminQRPaymentApproveView,
    AdminQRPaymentRejectView,
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