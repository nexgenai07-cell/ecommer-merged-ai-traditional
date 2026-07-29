# PATH: apps/products/views.py
from unittest import result
from urllib import request

from urllib import request
from .services import adjust_stock as adjust_stock_service
from django.db import transaction
from rest_framework import viewsets, permissions, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from django.db.models import Q

from .models import Product, ProductImage, ProductHistory, StockMovement
from .serializers import (
    ProductListSerializer,
    ProductDetailSerializer,
    ProductCreateUpdateSerializer,
    ProductImageSerializer,
    LowStockProductSerializer,
    StockAdjustSerializer,
)
from apps.users.permissions import IsAdmin
from core.pagination import StandardResultsPagination


class ProductViewSet(viewsets.ModelViewSet):
    """
    GET    /api/v1/products/             -> list (anyone)
    POST   /api/v1/products/             -> create (admin only)
    GET    /api/v1/products/{id}/        -> retrieve (anyone)
    PUT    /api/v1/products/{id}/        -> update (admin only)
    DELETE /api/v1/products/{id}/        -> soft delete (admin only)

    GET    /api/v1/products/search/      -> filtered search (anyone)
    GET    /api/v1/products/low-stock/   -> below threshold (admin only)

    POST   /api/v1/products/{id}/images/                       -> add image (admin only)
    DELETE /api/v1/products/{id}/images/{image_id}/             -> remove image (admin only)
    PUT    /api/v1/products/{id}/images/{image_id}/set-primary/ -> set primary (admin only)

    POST   /api/v1/products/{id}/stock/adjust/                  -> atomic stock adjustment (admin only)
    """
    filter_backends = [filters.SearchFilter]
    search_fields = ['name', 'description']
    # FIX: pagination_class add ki gayi — pehle koi pagination class kahin
    # set nahi thi (na globally, na yahan), isliye GET /products/ plain
    # array bhejta tha jab ke doc {count, next, previous, results} promise
    # karta hai. Ye standard list() action (list/retrieve/create/update/
    # destroy) ke liye hai — @action se bane custom endpoints (search,
    # low-stock) is class ko automatically use nahi karte, unhe neeche
    # manually paginate_queryset() call karke lagaya gaya hai.
    pagination_class = StandardResultsPagination

    def get_queryset(self):
      qs = Product.objects.filter(
        is_delete=False
      ).select_related(
        "category"
      ).prefetch_related(
        "images"
       )

    # Customers only see published products.
      if self.action in ["list", "retrieve", "search"]:
        if not (
            self.request.user.is_authenticated
            and self.request.user.role == "admin"
        ):
            qs = qs.filter(is_active=True)

      return qs

    def get_serializer_class(self):
        if self.action == 'list' or self.action == 'search':
            return ProductListSerializer
        if self.action in ['create', 'update', 'partial_update']:
            return ProductCreateUpdateSerializer
        if self.action == 'adjust_stock':
            return StockAdjustSerializer
        return ProductDetailSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'search']:
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated(), IsAdmin()]

    def perform_destroy(self, instance):
       """
    Soft delete the product.

    Only mark the record as deleted.
    Do NOT modify is_active because it is used independently
    as the Publish/Draft toggle.
    """
       instance.is_delete = True
       instance.save(update_fields=["is_delete", "updated_at"])

    def update(self, request, *args, **kwargs):
        instance = self.get_object()

        old_price = instance.price
        old_stock = instance.stock

        # Copy request data because request.data is immutable
        data = request.data.copy()

        # Amount to add to existing stock
        stock_to_add = int(data.get("stock_to_add", 0) or 0)

        if stock_to_add > 0:
            data["stock"] = old_stock + stock_to_add

        serializer = self.get_serializer(
            instance,
            data=data,
            partial=kwargs.pop("partial", False),
        )

        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        instance.refresh_from_db()

        if instance.price != old_price or instance.stock != old_stock:
            ProductHistory.objects.create(
                product=instance,
                changed_by=request.user,
                old_price=old_price,
                new_price=instance.price,
                old_stock=old_stock,
                new_stock=instance.stock,
                reason=f"Added {stock_to_add} units" if stock_to_add > 0 else "Product updated",
            )

        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='search')
    def search(self, request):
        """
        GET /api/v1/products/search/?q=phone&category_id=1&min_price=1000&max_price=50000&in_stock=true&ordering=-created_at&page=1

        FIXES applied here (Bug Report — /api/v1/products/search/):
          1. category_id — was reading request.query_params.get('category')
             (missing '_id'), so the frontend's ?category_id=6 was NEVER
             read and the filter never applied. Fixed to read 'category_id'.
          2. ordering — was not handled at all despite being a documented
             query param; now applies it via .order_by(), with a safe
             whitelist so random field names can't be passed in.
          3. pagination — was returning a plain array via
             Response(serializer.data); now uses paginate_queryset() /
             get_paginated_response() so the shape matches the documented
             {count, next, previous, results}, same as the standard list().
        """
        qs = self.get_queryset()

        q = request.query_params.get('q')
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q))

        # FIX (Bug 1): 'category_id' ab sahi se padha ja raha hai.
        category_id = request.query_params.get('category_id')
        if category_id:
            qs = qs.filter(category_id=category_id)

        min_price = request.query_params.get('min_price')
        if min_price:
            qs = qs.filter(price__gte=min_price)

        max_price = request.query_params.get('max_price')
        if max_price:
            qs = qs.filter(price__lte=max_price)

        in_stock = request.query_params.get('in_stock')
        if in_stock == 'true':
            qs = qs.filter(stock__gt=0)

        # FIX: 'ordering' param ab handle ho raha hai (pehle ignore hota tha).
        # Sirf inhi fields pe ordering allow hai — kisi bhi arbitrary column
        # name se sort karne ki request ko silently ignore kar dete hain
        # taake koi unexpected DB error na aaye.
        allowed_ordering_fields = {
            'created_at', '-created_at',
            'price', '-price',
            'name', '-name',
        }
        ordering = request.query_params.get('ordering')
        if ordering in allowed_ordering_fields:
            qs = qs.order_by(ordering)

        # FIX (Bug 2): pagination ab standard list() jaisi hi hai.
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = ProductListSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = ProductListSerializer(qs, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='low-stock',
            permission_classes=[permissions.IsAuthenticated, IsAdmin])
    def low_stock(self, request):
        """
        GET /api/v1/products/low-stock/ — products at or below their threshold

        FIX (Postman testing — 09 Jul 2026): doc ke mutabiq response mein
        sirf id, name, stock, low_stock_threshold hone chahiye. Pehle ye
        ProductListSerializer use kar raha tha jismein low_stock_threshold
        field hi nahi thi (wo serializer public product listing ke liye
        bana hai), is liye field kabhi response mein aati hi nahi thi.
        Ab isके liye alag, chota LowStockProductSerializer use ho raha hai
        jo sirf doc-required fields return karta hai.
        """
        qs = Product.objects.filter(
    is_active=True,
    is_delete=False,
)

        # Compare stock vs threshold in Python (clear and simple for small catalogs)
        low_stock_products = [p for p in qs if p.stock <= p.low_stock_threshold]
        serializer = LowStockProductSerializer(low_stock_products, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='images',
            permission_classes=[permissions.IsAuthenticated, IsAdmin],
            parser_classes=[MultiPartParser, FormParser])
    def upload_image(self, request, pk=None):
        """POST /api/v1/products/{id}/images/ — multipart form, field name: image"""
        product = self.get_object()
        image_file = request.FILES.get('image')

        if not image_file:
            return Response({'error': 'No image file provided.'}, status=status.HTTP_400_BAD_REQUEST)

        is_first_image = not product.images.exists()

        product_image = ProductImage.objects.create(
            product=product,
            image=image_file,
            is_primary=is_first_image,
        )

        return Response(
            ProductImageSerializer(product_image, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['delete'], url_path='images/(?P<image_id>[^/.]+)',
            permission_classes=[permissions.IsAuthenticated, IsAdmin])
    def delete_image(self, request, pk=None, image_id=None):
        """DELETE /api/v1/products/{id}/images/{image_id}/"""
        product = self.get_object()
        try:
            image = product.images.get(id=image_id)
        except ProductImage.DoesNotExist:
            return Response({'error': 'Image not found.'}, status=status.HTTP_404_NOT_FOUND)

        was_primary = image.is_primary
        image.delete()

        # If we deleted the primary image, promote another one automatically
        if was_primary:
            next_image = product.images.first()
            if next_image:
                next_image.is_primary = True
                next_image.save()

        return Response(status=status.HTTP_204_NO_CONTENT)
    

    @action(
        detail=True,
        methods=["post"],
        url_path="stock/adjust",
        permission_classes=[permissions.IsAuthenticated, IsAdmin],
    )
    def adjust_stock(self, request, pk=None):
        serializer = StockAdjustSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product = self.get_object()

        result = adjust_stock_service(
            product=product,
            delta=serializer.validated_data["delta"],
            reason=serializer.validated_data["reason"],
            changed_by=request.user,
            note=serializer.validated_data.get("note", ""),
        )

        return Response(result)


    @action(
        detail=True,
        methods=['put'],
        url_path='images/(?P<image_id>[^/.]+)/set-primary',
        permission_classes=[permissions.IsAuthenticated, IsAdmin]
    )
    def set_primary_image(self, request, pk=None, image_id=None):
        """
        PUT /api/v1/products/{id}/images/{image_id}/set-primary/
        """
        product = self.get_object()

        try:
            image = product.images.get(id=image_id)
        except ProductImage.DoesNotExist:
            return Response(
                {"error": "Image not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Remove primary flag from all images
        product.images.update(is_primary=False)

        # Make selected image primary
        image.is_primary = True
        image.save()

        return Response(
            {
                "message": "Primary image updated.",
                "image_id": image.id,
            },
            status=status.HTTP_200_OK,
        )