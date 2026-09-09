from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response

from .models import Category
from .serializers import CategorySerializer
from apps.users.permissions import IsAdmin
from apps.ai.audit import log_manual_admin_action as log_admin_action


class CategoryViewSet(viewsets.ModelViewSet):
    """
    GET    /api/v1/categories/       -> list (anyone)
    POST   /api/v1/categories/       -> create (admin only)
    GET    /api/v1/categories/{id}/  -> retrieve (anyone)
    PUT    /api/v1/categories/{id}/  -> update (admin only)
    DELETE /api/v1/categories/{id}/  -> soft delete (admin only)

    GET    /api/v1/categories/check-name/
           -> check category name availability (admin only)
    """

    serializer_class = CategorySerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    pagination_class = None

    def get_queryset(self):
        queryset = Category.objects.filter(
            is_delete=False
        ).order_by("name")

        # Customers and guests only see active categories.
        # Admins see both active and inactive categories.
        if not (
            self.request.user.is_authenticated
            and self.request.user.role == "admin"
        ):
            queryset = queryset.filter(is_active=True)

        return queryset

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

        queryset = Category.objects.filter(
            name__iexact=name
        )

        if exclude_id:
            queryset = queryset.exclude(pk=exclude_id)

        return Response(
            {"exists": queryset.exists()},
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

        user_store = self.request.user.stores.first()

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