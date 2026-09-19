# PATH: apps/products/urls.py

from rest_framework.routers import DefaultRouter
from django.urls import path

from .views import ProductViewSet
from .bulk_views import ProductBulkDeleteView

router = DefaultRouter()
router.register('', ProductViewSet, basename='product')

# NEW (Sep 2026 — Bulk Actions): "bulk-delete/" must come BEFORE the router
# urls — the router's "<pk>/" pattern matches any single string, so it
# would otherwise swallow "bulk-delete/".
urlpatterns = [
    path(
        'bulk-delete/',
        ProductBulkDeleteView.as_view(),
        name='product-bulk-delete',
    ),
] + router.urls