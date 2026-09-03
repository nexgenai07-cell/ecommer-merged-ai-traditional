from django.urls import path

from .views import (
    CreatePaymentIntentView,
    StripeWebhookView,
    QRProofUploadView,
    AdminQRPaymentPendingView,
    AdminQRPaymentApproveView,
    AdminQRPaymentRejectView,
)

# FIX (Cross-check, Sep 2026): every other app in this project (orders,
# returns, complaints, customers) splits customer-facing and admin-facing
# endpoints into two separate urlpatterns lists, mounted at two different
# prefixes in core/urls.py (e.g. orders/urls.py -> urlpatterns at
# /api/v1/orders/ + admin_order_urlpatterns at /api/v1/admin/orders/).
# This file used to be a single combined list with the admin paths
# manually prefixed "admin/qr/...", which only produces the spec's
# required /api/v1/admin/payments/qr/... paths if core/urls.py happens to
# mount this whole file at /api/v1/admin/payments/ AND ALSO at
# /api/v1/payments/ for the customer routes — i.e. mounted twice, which
# the single-list shape doesn't signal at all and the rest of the project
# never does. The views' own docstrings already declare the intended full
# paths (GET /api/v1/admin/payments/qr/pending/, PUT .../approve/,
# .../reject/, POST /api/v1/payments/qr/proof/) — splitting into two
# lists here, the same way orders/urls.py does, is what actually gets
# there.
#
# core/urls.py (not included in this apps/ export) needs to mount:
#   urlpatterns             -> /api/v1/payments/
#   admin_payment_urlpatterns -> /api/v1/admin/payments/
# the same way it already mounts admin_order_urlpatterns at
# /api/v1/admin/orders/, admin_customer_urlpatterns at
# /api/v1/admin/customers/, etc.

# Customer-facing (mounted at /api/v1/payments/)
urlpatterns = [
    # Stripe
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

    # QR Payment - Customer
    path(
        "qr/proof/",
        QRProofUploadView.as_view(),
        name="qr-proof-upload",
    ),
]

# Admin-facing (mounted at /api/v1/admin/payments/)
admin_payment_urlpatterns = [
    path(
        "qr/pending/",
        AdminQRPaymentPendingView.as_view(),
        name="admin-qr-pending",
    ),
    path(
        "qr/<str:order_number>/approve/",
        AdminQRPaymentApproveView.as_view(),
        name="admin-qr-approve",
    ),
    path(
        "qr/<str:order_number>/reject/",
        AdminQRPaymentRejectView.as_view(),
        name="admin-qr-reject",
    ),
]