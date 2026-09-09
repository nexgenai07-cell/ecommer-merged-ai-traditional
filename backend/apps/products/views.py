# PATH: apps/products/views.py
from rest_framework import viewsets, permissions, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from django.db.models import Q, F

from .services import adjust_stock as adjust_stock_service
from .models import Product, ProductImage, ProductHistory
from .serializers import (
    ProductListSerializer,
    ProductDetailSerializer,
    ProductCreateUpdateSerializer,
    ProductImageSerializer,
    LowStockProductSerializer,
    StockAdjustSerializer,
)
from apps.users.permissions import IsAdmin
from apps.ai.audit import log_manual_admin_action as log_admin_action
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

    GET    /api/v1/products/check-name/                         -> live name-availability check (admin only)
    GET    /api/v1/products/check-sku/                           -> live SKU-availability check (admin only)
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
            'category'
        ).prefetch_related(
            'images'
        )

        if self.action in ['list', 'retrieve', 'search']:
            if not (
                self.request.user.is_authenticated
                and self.request.user.role == 'admin'
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
        Soft delete product.
        """
        instance.is_active = False
        instance.is_delete = True
        instance.save(
            update_fields=[
                "is_active",
                "is_delete",
            ]
        )

        # FIX (Frontend Bug Report — Audit Logs, Sep 2026): Create/Update/
        # Delete Product never wrote to the shared AuditLog table that
        # powers Admin — List Audit Logs (API 82) and the System Activity
        # Logs widget — only Adjust Stock did (and to a different,
        # product-specific StockMovement table). Logged here now.
        log_admin_action(
            store=instance.store,
            user=self.request.user,
            action="delete_product",
            entity="product",
            entity_id=instance.id,
            old_data={"name": instance.name, "is_active": True, "is_delete": False},
            new_data={"name": instance.name, "is_active": False, "is_delete": True},
            request=self.request,
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product = serializer.save()

        image = request.FILES.get("image")

        if image:
            ProductImage.objects.create(
                product=product,
                image=image,
                is_primary=True,
            )

        log_admin_action(
            store=product.store,
            user=request.user,
            action="create_product",
            entity="product",
            entity_id=product.id,
            new_data={
                "name": product.name,
                "price": str(product.price),
                "total_stock": product.total_stock,
            },
            request=request,
        )

        response_serializer = ProductDetailSerializer(
            product,
            context={"request": request},
        )

        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )

    # FIX (Frontend Bug Report — Audit Logs, Sep 2026): this is the method
    # ModelViewSet.update() actually calls — the "update" method defined
    # further below is dead code (it's nested inside create() above due to
    # an indentation bug, so it never runs; the default
    # UpdateModelMixin.update() handles PUT/PATCH instead, which calls
    # perform_update()). Logs the update here rather than relying on that
    # dead code path.
    def perform_update(self, serializer):
        instance = serializer.instance
        old_data = {
            "name": instance.name,
            "price": str(instance.price),
            "total_stock": instance.total_stock,
            "is_active": instance.is_active,
        }

        product = serializer.save()

        log_admin_action(
            store=product.store,
            user=self.request.user,
            action="update_product",
            entity="product",
            entity_id=product.id,
            old_data=old_data,
            new_data={
                "name": product.name,
                "price": str(product.price),
                "total_stock": product.total_stock,
                "is_active": product.is_active,
            },
            request=self.request,
        )

        def update(self, request, *args, **kwargs):
            instance = self.get_object()

            old_price = instance.price
            # BUG FIX (cross-check, Sep 2026 — PDF Part 2 Item 5): this used
            # to read/compare instance.stock, the DEPRECATED field that
            # nothing else in the app writes to anymore (stock changes go
            # through total_stock). Since instance.stock never changes,
            # `instance.stock != old_stock` below was always False, so a
            # ProductHistory row was NEVER created when an admin adjusted
            # stock via this endpoint (stock_to_add) — silently breaking the
            # audit trail for this path even though total_stock really did
            # change. Reading/comparing total_stock instead fixes it. Also
            # dropped the dead `data["stock"] = ...` line — "stock" isn't in
            # ProductCreateUpdateSerializer.Meta.fields, so it was silently
            # ignored anyway; the serializer's own update() already applies
            # stock_to_add to total_stock correctly.
            old_stock = instance.total_stock

            # Copy request data because request.data is immutable
            data = request.data.copy()

            # Amount to add to existing stock
            stock_to_add = int(data.get("stock_to_add", 0) or 0)

            serializer = self.get_serializer(
                instance,
                data=data,
                partial=kwargs.pop("partial", False),
            )

            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)

            instance.refresh_from_db()

            if instance.price != old_price or instance.total_stock != old_stock:
                ProductHistory.objects.create(
                    product=instance,
                    changed_by=request.user,
                    old_price=old_price,
                    new_price=instance.price,
                    old_stock=old_stock,
                    new_stock=instance.total_stock,
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
          4. FIX (A1): 'q' ab sirf name/description nahi, sku bhi match
             karta h — ?q=ELE-BUL-A1C9 ab us product ko dhoond leta h
             chahe wo string name mei kahin na ho.
          5. FIX (A1): 'in_stock=false' pehle silently ignore ho raha tha
             (sirf 'true' check hota tha), is liye out-of-stock filter
             kabhi lagta hi nahi tha aur count hamesha poore catalog ka
             aata tha. Ab 'false' explicitly stock<=0 pe filter karta h.
          6. FIX (A1/E3): 'category_id' ab multiple values accept karta h
             — comma-separated (?category_id=5,8) aur repeated
             (?category_id=5&category_id=8) dono formats chalte hain.
          7. NEW (Follow-up v8, item 1): 'status' param — out_of_stock /
             low_stock / healthy — for the Inventory Alerts page. Combines
             with every other filter above in a single request.
        """
        qs = self.get_queryset()

        q = request.query_params.get('q')
        if q:
            qs = qs.filter(
                Q(name__icontains=q) |
                Q(description__icontains=q) |
                Q(sku__icontains=q)
            )

        # FIX (Bug 1 / A1 / E3): 'category_id' ab sahi se padha ja raha hai,
        # aur ek se zyada values bhi accept karta h.
        category_id_values = request.query_params.getlist('category_id')
        category_ids = []
        for raw in category_id_values:
            category_ids.extend([v.strip() for v in raw.split(',') if v.strip()])
        if category_ids:
            qs = qs.filter(category_id__in=category_ids).distinct()

        min_price = request.query_params.get('min_price')
        if min_price:
            qs = qs.filter(price__gte=min_price)

        max_price = request.query_params.get('max_price')
        if max_price:
            qs = qs.filter(price__lte=max_price)

        # FIX (Cross-check, Sep 2026 — PDF Part 2 Item 5): this filter was
        # still reading the deprecated 'stock' field, which nothing in the
        # codebase writes to anymore (checkout/confirm/cancel/reinstate
        # all only touch total_stock/reserved_stock now), so 'stock' sits
        # frozen at whatever it was on creation and this filter was
        # effectively broken for every real product. available_stock
        # isn't a DB column, so it's expressed with F() instead.
        in_stock = request.query_params.get('in_stock')
        if in_stock is not None:
            if in_stock.lower() == 'true':
                qs = qs.filter(total_stock__gt=F('reserved_stock'))
            elif in_stock.lower() == 'false':
                qs = qs.filter(total_stock__lte=F('reserved_stock'))

        # NEW (Follow-up v8, item 1): 'status' — combined stock-health
        # filter for the admin Inventory Alerts page. Separate from
        # 'in_stock' above (that one only knows zero-vs-not-zero; this one
        # also needs the per-product low_stock_threshold to tell "low" from
        # "healthy" apart), so both params can keep working independently.
        #   out_of_stock -> available_stock == 0
        #   low_stock    -> available_stock > 0 AND available_stock <= low_stock_threshold
        #   healthy      -> available_stock > low_stock_threshold
        # Unknown/garbage values are ignored rather than erroring, same
        # convention as 'ordering' below.
        # FIX (Cross-check, Sep 2026 — PDF Part 2 Item 5): same 'stock' ->
        # available_stock (total_stock - reserved_stock) fix as in_stock
        # above.
        status_param = request.query_params.get('status')
        if status_param == 'out_of_stock':
            qs = qs.filter(total_stock__lte=F('reserved_stock'))
        elif status_param == 'low_stock':
            qs = qs.annotate(
                _available_stock=F('total_stock') - F('reserved_stock')
            ).filter(_available_stock__gt=0, _available_stock__lte=F('low_stock_threshold'))
        elif status_param == 'healthy':
            qs = qs.annotate(
                _available_stock=F('total_stock') - F('reserved_stock')
            ).filter(_available_stock__gt=F('low_stock_threshold'))

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

        # FIX (Cross-check, Sep 2026 — PDF Part 2 Item 5): was comparing
        # p.stock (the deprecated field, frozen since nothing updates it
        # anymore) against the threshold, so this endpoint was comparing
        # stale/zero data instead of real stock. Uses available_stock
        # (total_stock - reserved_stock), same as everywhere else post
        # Reserved Stock change.
        low_stock_products = [p for p in qs if p.available_stock <= p.low_stock_threshold]
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

    @action(
        detail=False,
        methods=["get"],
        url_path="check-name",
        permission_classes=[permissions.IsAuthenticated, IsAdmin],
    )
    def check_name(self, request):
        """
        GET /api/v1/products/check-name/?name=Perfume&exclude_id=15

        Checks whether a product name already exists.

        Matching:
        - case-insensitive
        - trimmed
        - exclude_id ignored when editing the same product
        """
        name = request.query_params.get("name")

        if name is None:
            return Response(
                {"detail": "name query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        name = name.strip()

        if not name:
            return Response(
                {"detail": "name query parameter cannot be empty."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = Product.objects.filter(name__iexact=name)

        exclude_id = request.query_params.get("exclude_id")

        if exclude_id:
            qs = qs.exclude(pk=exclude_id)

        return Response(
            {"exists": qs.exists()},
            status=status.HTTP_200_OK,
        )

    @action(
        detail=False,
        methods=["get"],
        url_path="check-sku",
        permission_classes=[permissions.IsAuthenticated, IsAdmin],
    )
    def check_sku(self, request):
        """
        GET /api/v1/products/check-sku/?sku=SKU123&exclude_id=15

        Checks whether a product SKU already exists.

        Matching:
        - exact stored value
        - case-sensitive
        - exclude_id ignored when editing the same product
        """
        sku = request.query_params.get("sku")

        if sku is None:
            return Response(
                {"detail": "sku query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        sku = sku.strip()

        if not sku:
            return Response(
                {"detail": "sku query parameter cannot be empty."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = Product.objects.filter(sku=sku)

        exclude_id = request.query_params.get("exclude_id")

        if exclude_id:
            qs = qs.exclude(pk=exclude_id)

        return Response(
            {"exists": qs.exists()},
            status=status.HTTP_200_OK,
        )