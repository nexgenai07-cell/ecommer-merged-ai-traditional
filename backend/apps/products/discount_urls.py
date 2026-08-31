# PATH: apps/products/discount_urls.py

from rest_framework.routers import DefaultRouter
from django.urls import path, include

from .discount_views import (
    DiscountViewSet,
    DiscountValidateView,
    DiscountCodeAvailabilityView,
)

router = DefaultRouter()
router.register('', DiscountViewSet, basename='discount')

urlpatterns = [
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