# PATH: apps/products/bulk_views.py   (NEW FILE, same folder as views.py)
#
# WHY THIS FILE EXISTS (Sep 2026 — Bulk Actions):
# Products and Discounts had no bulk delete endpoint, so the frontend was
# calling DELETE once per selected row. These are the real bulk versions:
#
#   POST /api/v1/products/bulk-delete/
#   POST /api/v1/discounts/bulk-delete/
#
# Request : {"ids": [1, 2, 3]}
# Response: EXACTLY the Bulk Delete Categories (API 27.2) shape —
#   200 OK  {"deleted_ids": [1, 2], "missing_ids": [3], "message": "2 product(s) deleted successfully."}
#   400     {"detail": "ids must be a non-empty list of product ids."}
# (an id that doesn't exist / is already deleted goes to missing_ids; it
# never fails the batch).
#
# Each item is deleted by calling the EXISTING perform_destroy() of the
# single-delete viewset, so it is the same soft delete as before:
#   - Product : is_active=False, is_delete=True, removed from every
#               customer's cart, audit log written.
#   - Discount: is_active=False, is_delete=True, updated_at saved,
#               audit log written.

from rest_framework import permissions
from rest_framework.views import APIView

from apps.users.permissions import IsAdmin
from core.bulk import MISSING, parse_bulk_identifiers, run_bulk, bulk_response

from .models import Product, Discount
from .views import ProductViewSet
from .discount_views import DiscountViewSet


class ProductBulkDeleteView(APIView):
    """POST /api/v1/products/bulk-delete/   Body: {"ids": [1, 2, 3]}"""

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def post(self, request):
        ids = parse_bulk_identifiers(
            request.data, "ids", kind="int", label="product ids"
        )

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
                return MISSING, None

            viewset.perform_destroy(product)
            return True, None

        done, missing_ids, failed = run_bulk(ids, process)
        return bulk_response(
            done, missing_ids, failed,
            ids_key="deleted_ids", noun="product(s)", verb="deleted",
            include_details=False,
        )


class DiscountBulkDeleteView(APIView):
    """POST /api/v1/discounts/bulk-delete/   Body: {"ids": [1, 2, 3]}"""

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def post(self, request):
        ids = parse_bulk_identifiers(
            request.data, "ids", kind="int", label="discount ids"
        )

        viewset = DiscountViewSet()
        viewset.request = request

        discounts = {
            discount.id: discount
            for discount in Discount.objects.filter(is_delete=False, id__in=ids)
        }

        def process(discount_id):
            discount = discounts.get(discount_id)
            if discount is None:
                return MISSING, None

            viewset.perform_destroy(discount)
            return True, None

        done, missing_ids, failed = run_bulk(ids, process)
        return bulk_response(
            done, missing_ids, failed,
            ids_key="deleted_ids", noun="discount(s)", verb="deleted",
            include_details=False,
        )