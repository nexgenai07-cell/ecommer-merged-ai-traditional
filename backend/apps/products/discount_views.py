# PATH: apps/products/discount_views.py

from rest_framework import viewsets, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.db.models import Q
from django.utils import timezone

from .models import Discount
from .discount_serializers import (
    DiscountSerializer,
    DiscountValidateSerializer,
)
from apps.users.permissions import IsAdmin
from apps.ai.audit import log_manual_admin_action as log_admin_action
from core.pagination import StandardResultsPagination

# Handles complete CRUD operations for discount coupons.
# Only admin users can create, update, view, or soft delete discounts.
class DiscountViewSet(viewsets.ModelViewSet):
    """
    GET/POST    /api/v1/discounts/
    GET/PUT/DELETE /api/v1/discounts/{id}/

    Admin only.

    DELETE is a SOFT DELETE:
    Instead of removing the row, is_active=False.

    FIX (ticket: "Discount Delete — Convert to Soft Delete, same as
    Category/Product"):
      1. DELETE already stopped hard-deleting the row and already set
         is_active=False here — that part was correct. Response shape
         is untouched (still the default 204 No Content), as requested.
      2. GET /discounts/ (admin list) already returns ALL coupons
         (active + inactive) via get_queryset() below — no change
         needed, confirmed.
      3. Checkout coupon validation already rejects inactive/soft-deleted
         coupons — see DiscountValidateSerializer.validate() in
         discount_serializers.py, which filters is_active=True. Confirmed,
         no change needed.
      4. RESTORE already works via the existing PUT /discounts/{id}/
         endpoint: 'is_active' is writable on DiscountSerializer (not in
         read_only_fields), so sending {"is_active": true} on update
         reactivates a coupon. No new endpoint added, as requested.

      The one actual bug fixed here: perform_destroy() was calling
      instance.save(update_fields=["is_active"]). Because
      Discount.updated_at uses auto_now=True, Django only writes columns
      listed in update_fields — so updated_at was silently NOT being
      persisted to the database on soft-delete, breaking the "last
      modified" audit trail. Fixed by including "updated_at" in
      update_fields below.
    """

    serializer_class = DiscountSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    # FIX (Frontend audit, Sep 2026): GET /discounts/ never read any
    # query params before this — switching filters/search/pagination in
    # the admin UI changed nothing about the response, so the frontend
    # was calling this with NO params and doing 100% of the
    # search/type-filter/status-filter/pagination in the browser.
    # pagination_class was also explicitly None; it's now
    # StandardResultsPagination (page/page_size, same as every other
    # admin list), which means this endpoint's response shape changes
    # from a plain array to {count, next, previous, results} — the
    # frontend call site needs to be updated to read `.results` /
    # send `page` & `page_size` to match.
    pagination_class = StandardResultsPagination

    ORDERING_MAP = {
        'created_at': 'created_at',
        '-created_at': '-created_at',
        'code': 'code',
        '-code': '-code',
        'value': 'value',
        '-value': '-value',
        'end_date': 'end_date',
        '-end_date': '-end_date',
    }

    # Returns all discounts (both active and inactive)
    # ordered by newest first for the admin panel.
    def get_queryset(self):
        """
        Only non-deleted discounts.

        Query Params:
        - search   (matches coupon code, case-insensitive partial)
        - type     (percent / fixed)
        - status   (active / inactive / expired — derived, not a stored column)

        - active   = is_active=True  AND end_date is still in the future
        - inactive = is_active=False (admin manually turned it off, regardless of end_date)
        - expired  = is_active=True  AND end_date has already passed
        """
        qs = Discount.objects.filter(is_delete=False)

        params = self.request.query_params

        search = params.get('search')
        if search:
            qs = qs.filter(code__icontains=search)

        discount_type = params.get('type')
        if discount_type in ('percent', 'fixed'):
            qs = qs.filter(type=discount_type)

        status_param = params.get('status')
        now = timezone.now()

        if status_param == 'active':
            qs = qs.filter(is_active=True, end_date__gte=now)
        elif status_param == 'inactive':
            qs = qs.filter(is_active=False)
        elif status_param == 'expired':
            qs = qs.filter(is_active=True, end_date__lt=now)

        ordering = params.get('ordering')
        qs = qs.order_by(self.ORDERING_MAP.get(ordering, '-created_at'))

        return qs

    # FIX (Frontend Bug Report — Audit Logs, Sep 2026): no admin write
    # endpoint besides Adjust Stock was writing to the shared AuditLog
    # table (API 82 / System Activity Logs). Logged for create/update/
    # delete here now.
    def perform_create(self, serializer):
        discount = serializer.save()
        log_admin_action(
            store=discount.store,
            user=self.request.user,
            action="create_discount",
            entity="discount",
            entity_id=discount.id,
            new_data={"code": discount.code, "type": discount.type, "value": str(discount.value)},
            request=self.request,
        )

    def perform_update(self, serializer):
        instance = serializer.instance
        old_data = {"code": instance.code, "value": str(instance.value), "is_active": instance.is_active}

        discount = serializer.save()

        log_admin_action(
            store=discount.store,
            user=self.request.user,
            action="update_discount",
            entity="discount",
            entity_id=discount.id,
            old_data=old_data,
            new_data={"code": discount.code, "value": str(discount.value), "is_active": discount.is_active},
            request=self.request,
        )

    # Performs a soft delete by marking the discount inactive
    # instead of permanently removing it from the database.
    def perform_destroy(self, instance):
        """
        Soft delete instead of permanently deleting.

        FIX: update_fields now includes "updated_at" as well as
        "is_active" — previously only "is_active" was listed, which
        meant the auto_now updated_at timestamp was computed in memory
        but never written to the database on delete/restore.
        """
        instance.is_active = False
        instance.is_delete = True

        instance.save(
            update_fields=[
                "is_active",
                "is_delete",
                "updated_at",
            ]
        )

        log_admin_action(
            store=instance.store,
            user=self.request.user,
            action="delete_discount",
            entity="discount",
            entity_id=instance.id,
            old_data={"code": instance.code, "is_active": True},
            new_data={"code": instance.code, "is_active": False},
            request=self.request,
        )


# Validates coupon codes submitted during checkout
# and calculates the applicable discount amount.
class DiscountValidateView(APIView):
    """
    POST /api/v1/discounts/validate/

    Validate a coupon during checkout.
    """
    permission_classes = [permissions.IsAuthenticated]

    # Validates the coupon using the serializer,
    # calculates the discount, and returns the final payable amount.
    def post(self, request):
        serializer = DiscountValidateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        discount = serializer.validated_data["discount"]
        order_amount = serializer.validated_data["order_amount"]

        if discount.type == "percent":
            discount_amount = (order_amount * discount.value) / 100
        else:
            discount_amount = discount.value

        # Discount cannot exceed order amount
        discount_amount = min(discount_amount, order_amount)

        return Response(
            {
                "valid": True,
                "code": discount.code,
                "discount_type": discount.type,
                "discount_value": discount.value,
                "discount_amount": round(discount_amount, 2),
                "final_amount": round(order_amount - discount_amount, 2),
            },
            status=status.HTTP_200_OK,
        )


class DiscountCodeAvailabilityView(APIView):
    """
    GET /api/v1/discounts/check-code/

    Checks whether a discount code already exists.

    Query parameters:
        code: required
        exclude_id: optional, used when editing an existing discount

    Response:
        {"exists": true}
        {"exists": false}
    """

    permission_classes = [
        permissions.IsAuthenticated,
        IsAdmin,
    ]

    def get(self, request):
        code = request.query_params.get("code")

        if not code:
            return Response(
                {"detail": "code is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Discount codes are stored uppercase.
        code = code.strip().upper()

        queryset = Discount.objects.filter(
            code__iexact=code,
            is_delete=False,
        )

        exclude_id = request.query_params.get("exclude_id")

        if exclude_id:
            queryset = queryset.exclude(id=exclude_id)

        return Response(
            {
                "exists": queryset.exists()
            },
            status=status.HTTP_200_OK,
        )