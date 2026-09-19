# PATH: apps/products/discount_urls.py

from rest_framework.routers import DefaultRouter
from django.urls import path, include

from .discount_views import (
    DiscountViewSet,
    DiscountValidateView,
    DiscountCodeAvailabilityView,
)
from .bulk_views import DiscountBulkDeleteView

router = DefaultRouter()
router.register('', DiscountViewSet, basename='discount')

urlpatterns = [
    # NEW (Sep 2026 — Bulk Actions): must stay ABOVE the router include
    # below (its "<pk>/" pattern would otherwise swallow "bulk-delete/").
    path(
        'bulk-delete/',
        DiscountBulkDeleteView.as_view(),
        name='discount-bulk-delete',
    ),
    path(
        'validate/',
        DiscountValidateView.as_view(),
        name='discount-validate',
    ),
    path(
        'check-code/',
        DiscountCodeAvailabilityView.as_view(),
        name='discount-code-availability',
    ),
    path(
        '',
        include(router.urls),
    ),
]