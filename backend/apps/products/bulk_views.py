# PATH: apps/products/bulk_views.py   (NEW FILE, same folder as views.py)
#
# WHY THIS FILE EXISTS (Sep 2026 — Bulk Actions):
# Products and Discounts had no bulk delete endpoint, so the frontend was
# calling DELETE once per selected row. These are the real bulk versions:
#
#   POST /api/v1/products/bulk-delete/
#   POST /api/v1/discounts/bulk-delete/
#
# Body (both):  {"ids": [1, 2, 3]}
#
# Each item is deleted by calling the EXISTING perform_destroy() of the
# single-delete viewset, so it is the same soft delete as before:
#   - Product : is_active=False, is_delete=True, removed from every
#               customer's cart, audit log written.
#   - Discount: is_active=False, is_delete=True, updated_at saved,
#               audit log written.
# Ids that don't exist / are already deleted are reported in "failed"
# (the rest are still deleted). See core/bulk.py for the response shape.

from rest_framework import permissions
from rest_framework.views import APIView

from apps.users.permissions import IsAdmin
from core.bulk import parse_bulk_identifiers, run_bulk, bulk_response

from .models import Product, Discount
from .views import ProductViewSet
from .discount_views import DiscountViewSet


class ProductBulkDeleteView(APIView):
    """POST /api/v1/products/bulk-delete/   Body: {"ids": [1, 2, 3]}"""

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def post(self, request):
        ids = parse_bulk_identifiers(request.data, "ids", kind="int")

        # perform_destroy() only needs self.request, so a bare viewset
        # instance is enough to reuse the exact single-delete logic.
        viewset = ProductViewSet()
        viewset.request = request

        # Same rule as the single DELETE: already soft-deleted products
        # are treated as "not found".
        products = {
            product.id: product
            for product in Product.objects.filter(is_delete=False, id__in=ids)
        }

        def process(product_id):
            product = products.get(product_id)
            if product is None:
                return False, "Product not found or already deleted."

            name = product.name
            viewset.perform_destroy(product)
            return True, {"name": name}

        succeeded, failed = run_bulk(ids, process)
        return bulk_response(succeeded, failed, "products", "deleted")


class DiscountBulkDeleteView(APIView):
    """POST /api/v1/discounts/bulk-delete/   Body: {"ids": [1, 2, 3]}"""

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def post(self, request):
        ids = parse_bulk_identifiers(request.data, "ids", kind="int")

        viewset = DiscountViewSet()
        viewset.request = request

        discounts = {
            discount.id: discount
            for discount in Discount.objects.filter(is_delete=False, id__in=ids)
        }

        def process(discount_id):
            discount = discounts.get(discount_id)
            if discount is None:
                return False, "Discount not found or already deleted."

            code = discount.code
            viewset.perform_destroy(discount)
            return True, {"code": code}

        succeeded, failed = run_bulk(ids, process)
        return bulk_response(succeeded, failed, "discounts", "deleted")