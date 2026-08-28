from rest_framework import viewsets, permissions, status
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response

from .models import Category
from .serializers import CategorySerializer
from apps.users.permissions import IsAdmin


class CategoryViewSet(viewsets.ModelViewSet):
    """
    GET    /api/v1/categories/       -> list (anyone)
    POST   /api/v1/categories/       -> create (admin only)
    GET    /api/v1/categories/{id}/  -> retrieve (anyone)
    PUT    /api/v1/categories/{id}/  -> update (admin only)
    DELETE /api/v1/categories/{id}/  -> soft delete (admin only)
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

    def perform_destroy(self, instance):
        """
        Soft delete category.
        """
        instance.is_active = False
        instance.is_delete = True

        instance.save(
            update_fields=["is_active", "is_delete"]
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