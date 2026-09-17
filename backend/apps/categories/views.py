# PATH: apps/categories/views.py

from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response
from django.db.models import Count

from .models import Category
from .serializers import CategorySerializer
from apps.users.permissions import IsAdmin
from apps.ai.audit import log_manual_admin_action as log_admin_action
from core.pagination import StandardResultsPagination


class CategoryViewSet(viewsets.ModelViewSet):
    """
    GET    /api/v1/categories/       -> list (anyone)
    POST   /api/v1/categories/       -> create (admin only)
    GET    /api/v1/categories/{id}/  -> retrieve (anyone)
    PUT    /api/v1/categories/{id}/  -> update (admin only)
    DELETE /api/v1/categories/{id}/  -> soft delete (admin only)

    GET    /api/v1/categories/check-name/
           -> check category name availability (admin only)

    POST   /api/v1/categories/bulk-delete/
           -> soft delete multiple categories at once (admin only)

    Query Params on the list endpoint (NEW — Frontend audit, Sep 2026):
    - search              (matches category name, case-insensitive partial)
    - start_date/end_date  (YYYY-MM-DD, against created_at)
    - ordering            (name / -name / created_at / -created_at /
                            product_count / -product_count; defaults to
                            'name' — same default the old Meta.ordering
                            gave everyone before)
    - page / page_size    (via StandardResultsPagination)

    FIX: this list used to have pagination_class = None and read no
    query params at all — admins searching, date-filtering, sorting, or
    paging the category table were doing 100% of that in the browser
    after downloading every category. Response shape changes from a
    plain array to {count, next, previous, results}, same as every
    other admin list in this app.
    """

    serializer_class = CategorySerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    pagination_class = StandardResultsPagination

    ORDERING_MAP = {
        "name": "name",
        "-name": "-name",
        "created_at": "created_at",
        "-created_at": "-created_at",
        "product_count": "_product_count",
        "-product_count": "-_product_count",
    }

    def get_queryset(self):
        queryset = Category.objects.filter(is_delete=False)

        # Customers and guests only see active categories.
        # Admins see both active and inactive categories.
        if not (
            self.request.user.is_authenticated
            and self.request.user.role == "admin"
        ):
            queryset = queryset.filter(is_active=True)

        params = self.request.query_params

        search = params.get("search")
        if search:
            queryset = queryset.filter(name__icontains=search)

        start_date = params.get("start_date")
        if start_date:
            queryset = queryset.filter(created_at__date__gte=start_date)

        end_date = params.get("end_date")
        if end_date:
            queryset = queryset.filter(created_at__date__lte=end_date)

        # product_count isn't a real column — the admin table's "Product
        # Count" column/sort needs it, so it's annotated here the same
        # way total_orders/total_spent are annotated for the admin
        # customer list, purely so the DB can filter/sort by it too.
        queryset = queryset.annotate(
            _product_count=Count("products", distinct=True)
        )

        ordering = params.get("ordering")
        queryset = queryset.order_by(self.ORDERING_MAP.get(ordering, "name"))

        return queryset

    def list(self, request, *args, **kwargs):
        """
        IMPORTANT: pagination is opt-in here, gated on the caller
        actually sending a `page` param.

        This endpoint has more than one consumer: the navbar, footer,
        and shop-page filter checkboxes all fetch this and expect the
        full flat array in one call (this was the documented, deliberate
        reason pagination_class was None before). The admin Categories
        table is the only consumer that needs search/date/ordering/
        pagination. Simply turning on pagination_class for the whole
        ViewSet would have paginated ALL of those callers, silently
        cutting the navbar/footer/filter list down to one page — so
        list() is overridden to only return the paginated
        {count, next, previous, results} shape when `page` is present;
        every other caller keeps getting the exact plain array they
        always did (search/date/ordering filters still apply either way
        if the admin table wants to use them without paging).
        """
        queryset = self.filter_queryset(self.get_queryset())

        if "page" in request.query_params:
            page = self.paginate_queryset(queryset)
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def get_permissions(self):
        if self.action in ["list", "retrieve"]:
            return [permissions.AllowAny()]

        return [
            permissions.IsAuthenticated(),
            IsAdmin(),
        ]

    @action(
        detail=False,
        methods=["get"],
        url_path="check-name",
        permission_classes=[permissions.IsAuthenticated, IsAdmin],
    )
    def check_name(self, request):
        """
        GET /api/v1/categories/check-name/?name=&exclude_id=

        Checks whether a category name already exists.

        Matching:
        - Case-insensitive
        - Leading/trailing spaces ignored

        exclude_id:
        - Used when editing an existing category
        - Prevents the category from being detected as a duplicate of itself
        """

        name = request.query_params.get("name", "").strip()
        exclude_id = request.query_params.get("exclude_id")

        if not name:
            return Response(
                {"detail": "name is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # FIX (Consistency check while documenting soft-delete name/SKU
        # reuse, Sep 2026): this must match the same is_delete=False
        # scope as the actual create/update uniqueness check — otherwise
        # this live-validation endpoint could say a name is "taken" for
        # a soft-deleted category, even though creating it would
        # actually succeed.
        queryset = Category.objects.filter(
            name__iexact=name,
            is_delete=False,
        )

        if exclude_id:
            queryset = queryset.exclude(pk=exclude_id)

        return Response(
            {"exists": queryset.exists()},
            status=status.HTTP_200_OK,
        )

    @action(
        detail=False,
        methods=["post"],
        url_path="bulk-delete",
        permission_classes=[permissions.IsAuthenticated, IsAdmin],
    )
    def bulk_delete(self, request):
        """
        POST /api/v1/categories/bulk-delete/
        Body: {"ids": [1, 2, 3]}

        Soft-deletes every category whose id is in `ids`.

        FIX (Bug report, Sep 2026): there was no bulk-delete endpoint at
        all — only DELETE /{id}/ existed. The admin "Delete Categories"
        bulk action had nothing to call.

        This intentionally does NOT behave like a loop of single
        DELETE /{id}/ calls, because that would 404 (and abort) the
        entire batch the moment one stale/already-deleted id was in the
        selection. Instead:
        - Only ids that currently exist and are not already soft-deleted
          are deleted.
        - Any id in the request that doesn't match a live category is
          reported back in `missing_ids` instead of raising an error,
          so the frontend can reconcile its selection state (e.g. a
          category that was deleted a moment ago by someone else, or a
          stale id left over in local selection state) instead of the
          whole bulk action failing.
        """

        ids = request.data.get("ids")

        if not isinstance(ids, list) or not ids:
            return Response(
                {"detail": "ids must be a non-empty list of category ids."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Only real, not-yet-deleted categories are eligible for deletion.
        queryset = Category.objects.filter(pk__in=ids, is_delete=False)

        found_ids = list(queryset.values_list("id", flat=True))
        missing_ids = [
            category_id for category_id in ids if category_id not in found_ids
        ]

        deleted_ids = []
        for instance in queryset:
            self.perform_destroy(instance)
            deleted_ids.append(instance.id)

        return Response(
            {
                "deleted_ids": deleted_ids,
                "missing_ids": missing_ids,
                "message": (
                    f"{len(deleted_ids)} category(ies) deleted successfully."
                ),
            },
            status=status.HTTP_200_OK,
        )

    def perform_create(self, serializer):
        """
        Create a new category for the logged-in admin's store.

        B49 FIX:
        Newly created categories must immediately be visible
        on the customer-facing site, so they are always created
        with is_active=True and is_delete=False.
        """

        # UPDATED (v4.0): related_name changed from 'stores' to
        # 'administered_stores' now that a store has multiple, equal
        # admins (Store.admins M2M) instead of a single owner.
        user_store = self.request.user.administered_stores.first()

        if not user_store:
            from rest_framework.exceptions import ValidationError

            raise ValidationError(
                {
                    "detail": (
                        "This admin user is not associated with any "
                        "store in the database."
                    )
                }
            )

        serializer.save(
            store=user_store,
            is_active=True,
            is_delete=False,
        )

        # FIX (Frontend Bug Report — Audit Logs, Sep 2026): no admin write
        # endpoint besides Adjust Stock was writing to the shared AuditLog
        # table (API 82 / System Activity Logs). Logged here now.
        log_admin_action(
            store=user_store,
            user=self.request.user,
            action="create_category",
            entity="category",
            entity_id=serializer.instance.id,
            new_data={"name": serializer.instance.name},
            request=self.request,
        )

    def perform_update(self, serializer):
        instance = serializer.instance
        old_data = {"name": instance.name, "is_active": instance.is_active}

        category = serializer.save()

        log_admin_action(
            store=category.store,
            user=self.request.user,
            action="update_category",
            entity="category",
            entity_id=category.id,
            old_data=old_data,
            new_data={"name": category.name, "is_active": category.is_active},
            request=self.request,
        )

    def perform_destroy(self, instance):
        """
        Soft delete category.
        """

        instance.is_active = False
        instance.is_delete = True

        instance.save(
            update_fields=["is_active", "is_delete"]
        )

        log_admin_action(
            store=instance.store,
            user=self.request.user,
            action="delete_category",
            entity="category",
            entity_id=instance.id,
            old_data={"name": instance.name, "is_active": True},
            new_data={"name": instance.name, "is_active": False},
            request=self.request,
        )

    def destroy(self, request, *args, **kwargs):
        """
        DELETE /api/v1/categories/{id}/

        Soft delete the category instead of permanently
        removing it from the database.
        """

        instance = self.get_object()
        self.perform_destroy(instance)

        return Response(
            {
                "message": "Category deleted successfully."
            },
            status=status.HTTP_200_OK,
        )