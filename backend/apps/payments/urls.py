from django.urls import path

from .views import (
    CreatePaymentIntentView,
    StripeWebhookView,
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
]


# Mount these at /api/v1/admin/payments/
admin_payment_urlpatterns = [
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