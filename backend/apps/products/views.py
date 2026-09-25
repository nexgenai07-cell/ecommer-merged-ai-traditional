# PATH: apps/products/views.py
from decimal import Decimal, InvalidOperation

from rest_framework import viewsets, permissions, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from django.db.models import Q, F, Case, When, Value, IntegerField

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
    GET    /api/v1/products/suggestions/ -> navbar type-ahead dropdown (anyone)
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

        if self.action in ['list', 'retrieve', 'search', 'suggestions']:
            if not (
                self.request.user.is_authenticated
                and self.request.user.role == 'admin'
            ):
                qs = qs.filter(is_active=True)

        return qs

    def get_serializer_class(self):
        if self.action in ('list', 'search', 'suggestions'):
            return ProductListSerializer
        if self.action in ['create', 'update', 'partial_update']:
            return ProductCreateUpdateSerializer
        if self.action == 'adjust_stock':
            return StockAdjustSerializer
        return ProductDetailSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'search', 'suggestions']:
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated(), IsAdmin()]

    def perform_destroy(self, instance):
        """
        Soft delete product.

        FIX (Bug report, Sep 2026): a soft-deleted product's row is never
        actually removed from the DB (is_delete=True is just a flag), so
        CartItem's on_delete=CASCADE never fires — every customer who had
        this product in their cart kept it there forever, and (until the
        CheckoutView fix) could even still check out with it. Any
        CartItem referencing this product is now deleted right here, the
        moment an admin deletes the product, so it disappears from every
        customer's cart immediately.
        """
        from django.db import transaction

        with transaction.atomic():
            instance.is_active = False
            instance.is_delete = True
            instance.save(
                update_fields=[
                    "is_active",
                    "is_delete",
                ]
            )

            # Local import to avoid a module-load-time dependency between
            # the products and cart apps (same pattern as the local
            # ValidationError import elsewhere in this codebase).
            from apps.cart.models import CartItem

            CartItem.objects.filter(product=instance).delete()

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

    def _parse_price_range(self, request):
        """
        Reads and validates ?min_price= / ?max_price= from the request.

        Returns (min_price_value, max_price_value, error_response):
          - on success  -> (Decimal-or-None, Decimal-or-None, None)
          - on failure  -> (None, None, Response(400)) — the caller just
                           has to `return` that response as-is.

        NEW (Sep 2026): pulled out of search() into this helper so the
        exact same validation + short user-friendly messages are shared by
        /products/search/ and /products/low-stock/ (which now also
        accepts min_price / max_price).
        """
        # FIX (Price range filter bug report, Sep 2026): min_price /
        # max_price were passed straight into the queryset with no
        # validation at all — negative values (e.g. min_price=-500) were
        # silently accepted, and a "reversed" range (min_price greater
        # than max_price, e.g. min_price=10000&max_price=5000) was also
        # silently accepted and just returned zero results instead of
        # telling the caller their range was invalid. Both are now
        # rejected with a clear 400 error before touching the queryset.
        #
        # FIX (User-friendly error messages, Sep 2026): the reversed-range
        # message used to be a long technical string ("...The range must
        # go from the smaller value to the larger value, e.g.
        # min_price=5000&max_price=10000.") that the admin panel showed
        # as-is in its toast. All three messages below are now short,
        # plain-language sentences meant to be shown directly to the user.
        # The response shape ({"error": "..."}) and 400 status are
        # unchanged, so nothing on the frontend needs to change. Also
        # rejects NaN / Infinity, which Decimal() accepts but which would
        # otherwise crash the comparisons below with a 500.
        min_price = request.query_params.get('min_price')
        max_price = request.query_params.get('max_price')

        min_price_value = None
        if min_price is not None and min_price != '':
            try:
                min_price_value = Decimal(min_price)
                if not min_price_value.is_finite():
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                return None, None, Response(
                    {"error": "Please enter a valid minimum price."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if min_price_value < 0:
                return None, None, Response(
                    {"error": "Minimum price cannot be negative."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        max_price_value = None
        if max_price is not None and max_price != '':
            try:
                max_price_value = Decimal(max_price)
                if not max_price_value.is_finite():
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                return None, None, Response(
                    {"error": "Please enter a valid maximum price."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if max_price_value < 0:
                return None, None, Response(
                    {"error": "Maximum price cannot be negative."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if (
            min_price_value is not None
            and max_price_value is not None
            and min_price_value > max_price_value
        ):
            return None, None, Response(
                {"error": "Minimum price cannot be greater than maximum price."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return min_price_value, max_price_value, None


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
          4b. UPDATED (Sep 2026 — search scope change, backend request): SKU
             ab 'q' mein match nahi hota — frontend ka navbar placeholder
             ab sirf "Search by product name or category" kehta hai, is
             liye backend bhi sirf name/category tak scope kar diya gaya
             hai (SKU pehle hi hata diya gaya tha; is round mein
             `description` bhi hata di gayi hai, kyunki frontend ka
             scope sirf do cheezon — product name aur category — tak
             hi limit karna tha, taake koi bhi teesra field match na ho
             jo placeholder se match na kare).
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
            # NEW (Frontend audit, Sep 2026): 'q' now also matches the
            # product's category name — previously a customer typing a
            # category name (e.g. "Shoes") into the navbar search got
            # zero results because q only matched name/description/sku,
            # even though the category itself exists.
            #
            # UPDATED (Sep 2026 — search scope change, backend request):
            # `description` removed from this filter (SKU was already
            # not matched here) — matching is now limited to exactly
            # product name + category, same two fields the navbar's
            # "Search by product name or category" placeholder promises,
            # and the same scope the /suggestions/ dropdown already uses.
            qs = qs.filter(
                Q(name__icontains=q) |
                Q(category__name__icontains=q)
            )

        # FIX (Bug 1 / A1 / E3): 'category_id' ab sahi se padha ja raha hai,
        # aur ek se zyada values bhi accept karta h.
        category_id_values = request.query_params.getlist('category_id')
        category_ids = []
        for raw in category_id_values:
            category_ids.extend([v.strip() for v in raw.split(',') if v.strip()])
        if category_ids:
            qs = qs.filter(category_id__in=category_ids).distinct()

        # Price range validation lives in _parse_price_range() so that
        # /search/ and /low-stock/ share exactly the same rules and
        # error messages. See the FIX notes inside that method.
        min_price_value, max_price_value, price_error = self._parse_price_range(request)
        if price_error is not None:
            return price_error

        if min_price_value is not None:
            qs = qs.filter(price__gte=min_price_value)

        if max_price_value is not None:
            qs = qs.filter(price__lte=max_price_value)

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
        # FIX (Frontend audit, Sep 2026): 'status' now accepts 2+ values
        # at once — comma-separated (?status=out_of_stock,low_stock) and
        # repeated (?status=out_of_stock&status=low_stock) both work, the
        # same way category_id already does above — so Inventory Alerts
        # can select multiple status tabs in a single request instead of
        # looping one request per status and merging in the browser.
        status_values = request.query_params.getlist('status')
        statuses = []
        for raw in status_values:
            statuses.extend([v.strip() for v in raw.split(',') if v.strip()])

        if statuses:
            qs = qs.annotate(
                _available_stock=F('total_stock') - F('reserved_stock')
        )
        status_filter = Q()
        if 'out_of_stock' in statuses:
            status_filter |= Q(_available_stock__lte=0)
        if 'low_stock' in statuses:
            status_filter |= Q(
            _available_stock__gt=0,
            _available_stock__lte=F('low_stock_threshold'),
        )
    # FIX: "healthy" aur "in_stock" dono accept karo — frontend jo bhi
    # naam bheje "In Stock" k liye, dono handle ho jayen
        if 'healthy' in statuses or 'in_stock' in statuses:
            status_filter |= Q(_available_stock__gt=F('low_stock_threshold'))

        qs = qs.filter(status_filter)  # FIX: unconditional — empty Q() bhi
                                     # sahi filter hai jab statuses non-empty ho


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
            serializer = ProductListSerializer(
                page, many=True, context=self.get_serializer_context()
            )
            return self.get_paginated_response(serializer.data)

        serializer = ProductListSerializer(
            qs, many=True, context=self.get_serializer_context()
        )
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='suggestions')
    def suggestions(self, request):
        """
        GET /api/v1/products/suggestions/?q=auto

        NEW (Sep 2026 — navbar search-suggestions bug fix):
        The navbar's type-ahead dropdown was showing products with no
        relation to what the customer typed (e.g. "auto" returning a
        microwave, "machine" returning shoes and a suit). Root cause:
        there was no backend endpoint built for a dropdown at all —
        /search/ exists but returns the full paginated catalog matching
        `q`, not a short ranked list meant for a live dropdown. This is
        a dedicated, lightweight endpoint for that dropdown:
          - only returns products that actually match `q` (name or
            category name) — nothing unrelated is ever returned
          - ranked by relevance: name starts with `q` first, then name
            contains `q`, then category matches
          - capped at 6 results, no pagination — a dropdown doesn't need
            {count, next, previous}, just a short list
          - empty/missing `q` returns an empty list instead of dumping
            the whole catalog into the dropdown

        UPDATED (Sep 2026 — search scope change, backend request): SKU
        removed from matching and from the relevance ranking, to match
        the navbar's "Search by product name or category" placeholder.
        """
        q = request.query_params.get('q', '').strip()
        if not q:
            return Response([])

        qs = self.get_queryset().filter(
            Q(name__icontains=q) |
            Q(category__name__icontains=q)
        ).distinct()

        qs = qs.annotate(
            _relevance=Case(
                When(name__istartswith=q, then=Value(0)),
                When(name__icontains=q, then=Value(1)),
                default=Value(3),
                output_field=IntegerField(),
            )
        ).order_by('_relevance', 'name')[:6]

        serializer = ProductListSerializer(
            qs, many=True, context=self.get_serializer_context()
        )
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

        FIX (Frontend bug report, Sep 2026): the "chota" serializer above
        had no price / category / image / sku / is_active, so the admin
        Products table showed Rs. 0, blank category, no image and "No" on
        website whenever Status = Low Stock was selected. It now returns
        every field ProductListSerializer does (the original 4 doc fields
        are all still there), and this endpoint also accepts
        min_price / max_price / ordering like /products/search/ does.

        FIX (Frontend audit, Sep 2026): this endpoint used to return the
        FULL unpaginated list with no search/category params, so
        selecting "Low Stock" together with a text search or a category
        filter made the frontend download everything and do the
        search-match, category-match, and pagination itself in the
        browser. Now accepts the same q / category_id / page / page_size
        params as /products/search/, applied server-side before the
        Python low-stock comparison below (which still has to happen in
        Python since available_stock/low_stock_threshold isn't a single
        DB column to filter/order by directly).
        """
        # FIX (Frontend bug report, Sep 2026): select_related/prefetch_related
        # added because the serializer below now returns category and
        # primary_image too — without them every row would trigger its own
        # extra DB queries.
        qs = Product.objects.filter(
            is_active=True,
            is_delete=False,
        ).select_related('category').prefetch_related('images')

        q = request.query_params.get('q')
        if q:
            qs = qs.filter(
                Q(name__icontains=q) |
                Q(description__icontains=q) |
                Q(sku__icontains=q) |
                Q(category__name__icontains=q)
            )

        category_id_values = request.query_params.getlist('category_id')
        category_ids = []
        for raw in category_id_values:
            category_ids.extend([v.strip() for v in raw.split(',') if v.strip()])
        if category_ids:
            qs = qs.filter(category_id__in=category_ids)

        # FIX (Frontend bug report, Sep 2026): price range and sorting
        # were completely ignored here, so picking Status = "Low Stock"
        # together with a Price range / Sort on the admin Products page
        # returned EVERY low-stock product, unfiltered and unsorted.
        # Same validation/messages as /products/search/.
        min_price_value, max_price_value, price_error = self._parse_price_range(request)
        if price_error is not None:
            return price_error

        if min_price_value is not None:
            qs = qs.filter(price__gte=min_price_value)

        if max_price_value is not None:
            qs = qs.filter(price__lte=max_price_value)

        # Same whitelist as /products/search/. 'id' is added as a
        # tie-breaker so products with an equal price/name don't jump
        # between pages. The Python list comprehension below keeps the
        # queryset order, so the sort survives the low-stock comparison.
        allowed_ordering_fields = {
            'created_at', '-created_at',
            'price', '-price',
            'name', '-name',
        }
        ordering = request.query_params.get('ordering')
        if ordering in allowed_ordering_fields:
            qs = qs.order_by(ordering, 'id')

        # FIX (Cross-check, Sep 2026 — PDF Part 2 Item 5): was comparing
        # p.stock (the deprecated field, frozen since nothing updates it
        # anymore) against the threshold, so this endpoint was comparing
        # stale/zero data instead of real stock. Uses available_stock
        # (total_stock - reserved_stock), same as everywhere else post
        # Reserved Stock change.
        low_stock_products = [p for p in qs if p.available_stock <= p.low_stock_threshold]

        # IMPORTANT: pagination is opt-in, gated on `page` actually being
        # sent — same reasoning as CategoryViewSet.list(). This endpoint
        # has two consumers: the small admin-dashboard "red-alert"
        # widget (documented as expecting a plain array, no wrapper) and
        # the fuller Low Stock admin table (which needs search/
        # category_id/pagination and will send page/page_size). Calling
        # paginate_queryset() unconditionally would always return the
        # {count, next, previous, results} shape and silently truncate
        # the dashboard widget to one page — so it's only used when the
        # caller explicitly asks for a page.
        if "page" in request.query_params:
            page = self.paginate_queryset(low_stock_products)
            serializer = LowStockProductSerializer(
                page, many=True, context=self.get_serializer_context()
            )
            return self.get_paginated_response(serializer.data)

        serializer = LowStockProductSerializer(
            low_stock_products, many=True, context=self.get_serializer_context()
        )
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

        # FIX (Consistency check while documenting soft-delete name/SKU
        # reuse, Sep 2026): scoped to is_delete=False to match the real
        # create/update uniqueness check — otherwise this endpoint could
        # say a name is "taken" for a soft-deleted product, even though
        # creating it would actually succeed.
        qs = Product.objects.filter(name__iexact=name, is_delete=False)

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

        # FIX (Consistency check while documenting soft-delete name/SKU
        # reuse, Sep 2026): scoped to is_delete=False to match the real
        # create/update uniqueness check — otherwise this endpoint could
        # say a SKU is "taken" for a soft-deleted product, even though
        # creating it would actually succeed.
        qs = Product.objects.filter(sku=sku, is_delete=False)

        exclude_id = request.query_params.get("exclude_id")

        if exclude_id:
            qs = qs.exclude(pk=exclude_id)

        return Response(
            {"exists": qs.exists()},
            status=status.HTTP_200_OK,
        )