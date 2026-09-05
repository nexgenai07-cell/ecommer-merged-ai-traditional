#apps/analytics/dashboard_views.py
import calendar
import datetime as dt
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import (
    Sum, Count, Min, Max, OuterRef, Subquery, IntegerField, Q, Value, DecimalField,
)
from django.db.models.functions import (
    TruncDate,
    TruncWeek,
    TruncMonth,
    TruncYear,
    Coalesce,
)
from django.utils import timezone

from rest_framework import permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.orders.models import Order, OrderItem, Customer
from apps.products.models import Product, Discount
from apps.social.models import SocialPost
from apps.returns.models import Return, Complaint
from apps.users.permissions import IsAdmin

def parse_date_range(request):
    """
    Reads:
    ?start_date=
    ?end_date=
    ?period=daily|weekly|monthly|yearly

    NOTE: This shared helper is used by SalesReportView, RevenueReportView,
    and OrdersAnalyticsView. Per the "Customer Growth — add quarter/year"
    ticket, this is intentionally left UNCHANGED — those endpoints were
    only ever tested with daily/weekly/monthly and should keep behaving
    exactly as before. CustomerGrowthView below has its own, separate
    period parsing so the new "quarter" / "year" values don't leak into
    (and potentially break) this shared helper or get_trunc_function().
    """
    start_date = request.query_params.get("start_date")
    end_date = request.query_params.get("end_date")
    period = request.query_params.get("period", "daily").lower()

    if period not in ["daily", "weekly", "monthly", "yearly"]:
        period = "daily"

    return start_date, end_date, period


def filter_orders_by_date(qs, start_date, end_date):
    if start_date:
        qs = qs.filter(created_at__date__gte=start_date)

    if end_date:
        qs = qs.filter(created_at__date__lte=end_date)

    return qs


def get_trunc_function(period):
    return {
        "daily": TruncDate,
        "weekly": TruncWeek,
        "monthly": TruncMonth,
        "yearly": TruncYear,
    }[period]


class DashboardView(APIView):
    """
    GET /api/v1/analytics/dashboard/


    High-level summary cards for the admin dashboard homepage.
    Cached for 5 minutes since this is called frequently but changes slowly.
    """
    permission_classes = [permissions.IsAuthenticated, IsAdmin]


    def get(self, request):
        cache_key = 'analytics_dashboard'
        cached = cache.get(cache_key)
        if cached:
            return Response(cached)


        today = timezone.now().date()
        last_30_days = today - timedelta(days=30)
        prev_30_days = last_30_days - timedelta(days=30)


        delivered_orders = Order.objects.exclude(status='cancelled')


        total_revenue = delivered_orders.aggregate(total=Sum('total_amount'))['total'] or 0
        total_orders = Order.objects.count()
        total_customers = Customer.objects.count()
        total_products = Product.objects.filter(
    is_active=True,
    is_delete=False,
        ).count()


        this_period_revenue = delivered_orders.filter(
            created_at__date__gte=last_30_days
        ).aggregate(total=Sum('total_amount'))['total'] or 0


        prev_period_revenue = delivered_orders.filter(
            created_at__date__gte=prev_30_days, created_at__date__lt=last_30_days
        ).aggregate(total=Sum('total_amount'))['total'] or 0


        this_period_orders = Order.objects.filter(created_at__date__gte=last_30_days).count()
        prev_period_orders = Order.objects.filter(
            created_at__date__gte=prev_30_days, created_at__date__lt=last_30_days
        ).count()


        def growth_pct(current, previous):
            if previous == 0:
                return '+0%' if current == 0 else '+100%'
            pct = ((current - previous) / previous) * 100
            sign = '+' if pct >= 0 else ''
            return f'{sign}{pct:.0f}%'


        today_revenue = delivered_orders.filter(created_at__date=today).aggregate(total=Sum('total_amount'))['total'] or 0
        today_orders = Order.objects.filter(created_at__date=today).count()


        # FIX (B45): Order.STATUS_CHOICES has "pending_payment", not a bare
        # "pending" — that value never matches any real order, so this
        # count was always wrong (usually 0). Corrected to the real value.
        pending_orders = Order.objects.filter(status='pending_payment').count()

        # FIX (B45): removed the dead first computation of
        # low_stock_products (it was calculated once with .count(), then
        # immediately overwritten below) — kept only the correct
        # threshold-based calculation.
        low_stock_products = sum(
            1
            for p in Product.objects.filter(
                is_active=True,
                is_delete=False,
            )
            if p.stock <= p.low_stock_threshold
        )

        data = {
         'total_revenue': total_revenue,
         'total_orders': total_orders,
         'total_customers': total_customers,
         'total_products': total_products,
         'revenue_growth': growth_pct(this_period_revenue, prev_period_revenue),
         'orders_growth': growth_pct(this_period_orders, prev_period_orders),
         'pending_orders': pending_orders,
         'low_stock_products': low_stock_products,
         'today_revenue': today_revenue,
         'today_orders': today_orders,
}


        cache.set(cache_key, data, timeout=300)  # 5 minutes
        return Response(data)



class SalesReportView(APIView):
    """
    GET /api/v1/analytics/sales/?start_date=&end_date=&period=daily|weekly|monthly|yearly
    """
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get(self, request):
        start_date, end_date, period = parse_date_range(request)

        qs = filter_orders_by_date(
            Order.objects.exclude(status="cancelled"),
            start_date,
            end_date,
        )

        trunc_fn = get_trunc_function(period)

        # FIX: units-sold ke liye seedha Sum("items__quantity") lagane se
        # Order-OrderItem join fan-out ho jata — jis order ke 2+ items hon
        # wo order Count("id")/Sum("total_amount") mein bhi multiple baar
        # count ho jata (galat total_orders/total_revenue). Isliye pehle
        # per-order units_sold ek scalar Subquery se nikalte hain (koi
        # join/fan-out nahi hota), phir bucket ke hisaab se normal
        # group-by/Sum chalta hai.
        units_subquery = (
            OrderItem.objects.filter(order=OuterRef("pk"))
            .values("order")
            .annotate(total=Sum("quantity"))
            .values("total")
        )

        qs = qs.annotate(
            units_sold=Coalesce(
                Subquery(units_subquery, output_field=IntegerField()), 0
            )
        )

        rows = (
            qs.annotate(bucket=trunc_fn("created_at"))
            .values("bucket")
            .annotate(
                total_orders=Count("id"),
                total_revenue=Sum("total_amount"),
                total_units=Sum("units_sold"),
            )
            .order_by("bucket")
        )

        data = []

        for row in rows:
            bucket = row["bucket"]

            # API contract:
            # yearly -> "2024-01-01"
            if period == "yearly":
                bucket = bucket.strftime("%Y-01-01")
            elif period == "monthly":
                bucket = bucket.strftime("%Y-%m-01")
            elif period == "daily":
                bucket = bucket.strftime("%Y-%m-%d")
            else:
                # weekly
                bucket = bucket.strftime("%Y-%m-%d")

            data.append(
                {
                    "date": bucket,
                    "total_orders": row["total_orders"],
                    "total_revenue": row["total_revenue"] or 0,
                    "total_units": row["total_units"] or 0,   # NEW
                }
            )

        return Response(
            {
                "period": period,
                "data": data,
            }
        )
        
class RevenueReportView(APIView):
    """
    GET /api/v1/analytics/revenue/?start_date=&end_date=&period=daily|weekly|monthly|yearly
    """
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get(self, request):
        start_date, end_date, period = parse_date_range(request)

        qs = filter_orders_by_date(
            Order.objects.exclude(status="cancelled"),
            start_date,
            end_date,
        )

        trunc_fn = get_trunc_function(period)

        rows = (
            qs.annotate(period_bucket=trunc_fn("created_at"))
            .values("period_bucket")
            .annotate(
                revenue=Sum("total_amount")
            )
            .order_by("period_bucket")
        )

        data = []

        for row in rows:
            bucket = row["period_bucket"]

            if period == "yearly":
                period_value = bucket.strftime("%Y")
            elif period == "monthly":
                period_value = bucket.strftime("%Y-%m")
            elif period == "daily":
                period_value = bucket.strftime("%Y-%m-%d")
            else:
                # weekly
                period_value = bucket.strftime("%Y-%m-%d")

            data.append(
                {
                    "period": period_value,
                    "revenue": row["revenue"] or 0,
                }
            )

        return Response(
            {
                "data": data,
            }
        )

class OrdersAnalyticsView(APIView):
    """
    GET /api/v1/analytics/orders/?start_date=&end_date=
    Returns order counts broken down by status — used for the status pie/bar chart.
    """
    permission_classes = [permissions.IsAuthenticated, IsAdmin]


    def get(self, request):
        start_date, end_date, _ = parse_date_range(request)
        qs = filter_orders_by_date(Order.objects.all(), start_date, end_date)


        breakdown = qs.values('status').annotate(count=Count('id')).order_by('status')


        return Response({
            'total': qs.count(),
            'by_status': list(breakdown),
        })



class BestSellersView(APIView):
    """GET /api/v1/analytics/products/best-sellers/?start_date=&end_date=&limit=5"""
    permission_classes = [permissions.IsAuthenticated, IsAdmin]


    def get(self, request):
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        limit = int(request.query_params.get("limit", 5))


        qs = OrderItem.objects.exclude(order__status="cancelled")


        if start_date:
            qs = qs.filter(order__created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(order__created_at__date__lte=end_date)


        data = (
            qs.values("product_id", "product_name")
              .annotate(
                  total_sold=Sum("quantity"),
                  total_revenue=Sum("total_price"),
              )
              .order_by("-total_sold")[:limit]
        )


        response = [
            {
                "product_id": item["product_id"],
                "name": item["product_name"],   # API docs expect "name"
                "total_sold": item["total_sold"],
                "total_revenue": item["total_revenue"],
            }
            for item in data
        ]


        return Response(response)


class LowPerformingProductsView(APIView):
    """GET /api/v1/analytics/products/low-performing/?limit=5 — least sold active products"""
    permission_classes = [permissions.IsAuthenticated, IsAdmin]


    def get(self, request):
        limit = int(request.query_params.get('limit', 5))


        sold_product_ids = (
            OrderItem.objects.exclude(order__status='cancelled')
            .values('product_id')
            .annotate(total_sold=Sum('quantity'))
        )
        sold_map = {row['product_id']: row['total_sold'] for row in sold_product_ids}


        products = Product.objects.filter(
    is_active=True,
    is_delete=False,
        )
        ranked = sorted(products, key=lambda p: sold_map.get(p.id, 0))[:limit]


        data = [
            {
                'product_id': p.id,
                'name': p.name,
                'total_sold': sold_map.get(p.id, 0),
                'stock': p.stock,
            }
            for p in ranked
        ]


        return Response(data)



class CustomerGrowthView(APIView):
    """
    GET /api/v1/analytics/customers/growth/?start_date=&end_date=&period=daily|weekly|monthly|quarter|year
    """
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    VALID_PERIODS = ["daily", "weekly", "monthly", "quarter", "year"]

    def get(self, request):
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        period = request.query_params.get("period", "daily").lower()

        if period not in self.VALID_PERIODS:
            period = "daily"

        qs = Customer.objects.all()
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)

        if period == "quarter":
            data = self._group_by_quarter(qs)
        elif period == "year":
            data = self._group_by_year(qs)
        else:
            data = self._group_by_simple_period(qs, period)

        return Response(data)

    def _group_by_simple_period(self, qs, period):
        """daily / weekly / monthly — same grouping as before, just with
        explicit string formatting instead of relying on default date
        serialization."""
        trunc_fn = {"daily": TruncDate, "weekly": TruncWeek, "monthly": TruncMonth}[period]

        rows = (
            qs.annotate(bucket=trunc_fn("created_at"))
              .values("bucket")
              .annotate(new_customers=Count("id"))
              .order_by("bucket")
        )

        result = []

        for row in rows:
            bucket = row["bucket"]

            if period == "monthly":
                period_value = bucket.strftime("%Y-%m")
            else:
                period_value = bucket.strftime("%Y-%m-%d")

            result.append({
                "period": period_value,
                "new_customers": row["new_customers"],
            })

        return result

    QUARTER_MONTH_RANGES = {
        1: (1, 3),
        2: (4, 6),
        3: (7, 9),
        4: (10, 12),
    }

    def _aware_bounds(self, start_naive, end_naive):
        if settings.USE_TZ:
            current_tz = timezone.get_current_timezone()
            start_naive = timezone.make_aware(start_naive, current_tz)
            end_naive = timezone.make_aware(end_naive, current_tz)
        return start_naive, end_naive

    def _group_by_quarter(self, qs):
        """
        Groups into FIXED calendar quarters using exact calendar boundaries.

        Q1: Jan 1  -> Mar 31
        Q2: Apr 1  -> Jun 30
        Q3: Jul 1  -> Sep 30
        Q4: Oct 1  -> Dec 31

        start_date/end_date only filter which records are considered.
        Grouping itself always uses fixed calendar quarters. Upper bounds
        use an exclusive "start of next period" (created_at__lt=next_dt)
        rather than an inclusive 23:59:59.999999 end, which sidesteps any
        microsecond-rounding edge cases entirely.
        """
        bounds = qs.aggregate(
            earliest=Min("created_at"),
            latest=Max("created_at"),
        )

        if bounds["earliest"] is None:
            return []

        data = []

        for year in range(bounds["earliest"].year, bounds["latest"].year + 1):

            for quarter, (start_month, end_month) in self.QUARTER_MONTH_RANGES.items():

                start_dt = dt.datetime(year, start_month, 1)

                if quarter == 4:
                    next_dt = dt.datetime(year + 1, 1, 1)
                else:
                    next_dt = dt.datetime(year, end_month + 1, 1)

                start_dt, next_dt = self._aware_bounds(start_dt, next_dt)

                count = qs.filter(
                    created_at__gte=start_dt,
                    created_at__lt=next_dt,
                ).count()

                if count:
                    data.append(
                        {
                            "period": f"{year}-Q{quarter}",
                            "new_customers": count,
                        }
                    )

        return data

    def _group_by_year(self, qs):
        """
        Groups into FIXED calendar years using explicit, inclusive
        datetime boundaries: YYYY-01-01T00:00:00.000000 ..
        YYYY-12-31T23:59:59.999999 — same precision-spec pattern as
        _group_by_quarter above, replacing the previous TruncYear
        implementation.
        """
        bounds = qs.aggregate(earliest=Min("created_at"), latest=Max("created_at"))
        if bounds["earliest"] is None:
            return []

        data = []
        for year in range(bounds["earliest"].year, bounds["latest"].year + 1):
            start_dt = dt.datetime(year, 1, 1)
            next_dt = dt.datetime(year + 1, 1, 1)

            start_dt, next_dt = self._aware_bounds(start_dt, next_dt)
            
            count = qs.filter(created_at__gte=start_dt, created_at__lt=next_dt,).count()
            if count:
                data.append({"period": f"{year}", "new_customers": count})

        return data



class InventoryAlertsView(APIView):
    """GET /api/v1/analytics/inventory/alerts/ — active products at/below their available-stock threshold"""
    permission_classes = [permissions.IsAuthenticated, IsAdmin]


    def get(self, request):
        # FIX (Cross-check, Sep 2026 — Inventory Alerts follow-up to v2
        # Item 5): this endpoint was missed when API 28/29/30/38 moved from
        # a single "stock" field to total_stock/reserved_stock/
        # available_stock. It kept using the old flat p.stock field (and
        # compared against total_stock, not available_stock), so a product
        # that was fully stocked on paper but mostly reserved by pending
        # orders never showed up here, while the page's own math made
        # everything look "Out of Stock" once the frontend started
        # expecting available_stock instead. available_stock is computed
        # the same way as API 38 (total_stock - reserved_stock) — same
        # underlying fields, no second/duplicate stock number.
        products = Product.objects.filter(
    is_active=True,
    is_delete=False,
    )
        alerts = []
        for p in products:
            available_stock = p.total_stock - p.reserved_stock
            if available_stock <= p.low_stock_threshold:
                alerts.append({
                    'product_id': p.id,
                    'name': p.name,
                    'total_stock': p.total_stock,
                    'reserved_stock': p.reserved_stock,
                    'available_stock': available_stock,
                    'low_stock_threshold': p.low_stock_threshold,
                })
        return Response(alerts)



class AnalyticsExportView(APIView):
    """
    GET /api/v1/analytics/export/?type=<type>&start_date=&end_date=
    Returns a CSV file for the requested report type.

    FIX (B1): 'type' was completely ignored before this fix - every
    request returned the exact same orders CSV no matter what type was
    passed. Now dispatches to a type-specific CSV export. The 10
    accepted values are exactly the ones the frontend already sends
    (confirmed, none needed renaming):
        sales, orders, discounts, inventory, returns, complaints,
        social_posts, customers, revenue, products
    A missing or unrecognized 'type' returns 400 with the full accepted
    list, instead of silently exporting orders.

    NOTE on column choices: the v7 doc didn't specify exact CSV columns
    per type (only that each type must export "a real CSV, not an
    error, not an empty file"), so the columns below are my best-effort
    pick of what's actually useful per report. If the frontend/product
    side wants specific columns for any of these, tell me and I'll
    adjust - these are easy to change.
    """
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    ALLOWED_TYPES = {
        'sales', 'orders', 'discounts', 'inventory', 'returns',
        'complaints', 'social_posts', 'customers', 'revenue', 'products',
    }

    def get(self, request):
        import csv
        from django.http import HttpResponse

        export_type = request.query_params.get('type')
        if export_type not in self.ALLOWED_TYPES:
            return Response(
                {
                    'error': "Invalid or missing 'type' parameter.",
                    'accepted_values': sorted(self.ALLOWED_TYPES),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        start_date, end_date, _ = parse_date_range(request)

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{export_type}_export.csv"'
        writer = csv.writer(response)

        handler = getattr(self, f'_export_{export_type}')
        handler(writer, start_date, end_date)

        return response

    # ---- per-type CSV handlers ----------------------------------------

    def _export_orders(self, writer, start_date, end_date):
        qs = filter_orders_by_date(Order.objects.all(), start_date, end_date)
        writer.writerow(['Order Number', 'Customer', 'Total Amount', 'Status', 'Payment Status', 'Created At'])
        for order in qs.select_related('customer', 'payment'):
            payment_status = order.payment.status if hasattr(order, 'payment') and order.payment else 'N/A'
            writer.writerow([
                order.order_number, order.customer.name, order.total_amount,
                order.status, payment_status, order.created_at,
            ])

    def _export_sales(self, writer, start_date, end_date):
        # Per-order sales record: same underlying orders as the 'orders'
        # export, trimmed/reshaped to what a sales report typically
        # needs (item count instead of payment status).
        qs = filter_orders_by_date(Order.objects.all(), start_date, end_date)
        writer.writerow(['Order Number', 'Customer', 'Items', 'Total Amount', 'Status', 'Created At'])
        for order in qs.select_related('customer').prefetch_related('items'):
            writer.writerow([
                order.order_number, order.customer.name, order.items.count(),
                order.total_amount, order.status, order.created_at,
            ])

    def _export_revenue(self, writer, start_date, end_date):
        # Revenue-recognized orders only - excludes cancelled /
        # pending_payment, same exclusion used for the customers
        # total_spent calculation elsewhere (A2), for consistency.
        qs = filter_orders_by_date(Order.objects.all(), start_date, end_date)
        qs = qs.exclude(status__in=['cancelled', 'pending_payment'])
        writer.writerow(['Order Number', 'Customer', 'Total Amount', 'Status', 'Created At'])
        for order in qs.select_related('customer'):
            writer.writerow([
                order.order_number, order.customer.name,
                order.total_amount, order.status, order.created_at,
            ])

    def _export_discounts(self, writer, start_date, end_date):
        qs = Discount.objects.filter(is_delete=False)
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)
        writer.writerow(['Code', 'Type', 'Value', 'Min Order Amount', 'Start Date', 'End Date', 'Is Active', 'Created At'])
        for d in qs:
            writer.writerow([
                d.code, d.type, d.value, d.min_order_amount or '',
                d.start_date, d.end_date, d.is_active, d.created_at,
            ])

    def _export_inventory(self, writer, start_date, end_date):
        # Inventory is a point-in-time snapshot (current stock levels),
        # so start_date/end_date are intentionally not applied here.
        qs = Product.objects.filter(is_delete=False).select_related('category')
        writer.writerow(['SKU', 'Name', 'Category', 'Stock', 'Low Stock Threshold', 'Is Active'])
        for p in qs:
            writer.writerow([
                p.sku, p.name, p.category.name if p.category else '',
                p.stock, p.low_stock_threshold, p.is_active,
            ])

    def _export_products(self, writer, start_date, end_date):
        qs = Product.objects.filter(is_delete=False).select_related('category')
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)
        writer.writerow(['SKU', 'Name', 'Category', 'Price', 'Stock', 'Is Active', 'Created At'])
        for p in qs:
            writer.writerow([
                p.sku, p.name, p.category.name if p.category else '',
                p.price, p.stock, p.is_active, p.created_at,
            ])

    def _export_returns(self, writer, start_date, end_date):
        qs = Return.objects.select_related('order', 'customer')
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)
        writer.writerow(['Order Number', 'Customer', 'Reason', 'Status', 'Created At', 'Resolved At'])
        for r in qs:
            writer.writerow([
                r.order.order_number, r.customer.name if r.customer else '',
                r.reason, r.status, r.created_at, r.resolved_at or '',
            ])

    def _export_complaints(self, writer, start_date, end_date):
        qs = Complaint.objects.select_related('customer', 'order')
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)
        writer.writerow(['ID', 'Customer', 'Order Number', 'Type', 'Status', 'Priority', 'Created At'])
        for c in qs:
            writer.writerow([
                c.id, c.customer.name, c.order.order_number if c.order else '',
                c.type, c.status, c.priority, c.created_at,
            ])

    def _export_social_posts(self, writer, start_date, end_date):
        qs = SocialPost.objects.all()
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)
        writer.writerow(['ID', 'Platform', 'Caption', 'Hashtags', 'Status', 'Created At'])
        for p in qs:
            writer.writerow([
                p.id, p.platform, p.caption, p.hashtags, p.status, p.created_at,
            ])

    def _export_customers(self, writer, start_date, end_date):
        # Same "exclude cancelled + pending_payment" total_orders /
        # total_spent logic as AdminCustomerListView (A2), for
        # consistency between the admin customers list and this export.
        qs = Customer.objects.all()
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)

        excluded_statuses = ['cancelled', 'pending_payment']
        qs = qs.annotate(
            _total_orders=Count(
                'orders', filter=~Q(orders__status__in=excluded_statuses), distinct=True,
            ),
            _total_spent=Coalesce(
                Sum('orders__total_amount', filter=~Q(orders__status__in=excluded_statuses)),
                Value(0), output_field=DecimalField(),
            ),
        )
        writer.writerow(['Name', 'Phone', 'Email', 'Total Orders', 'Total Spent', 'Created At'])
        for c in qs:
            writer.writerow([
                c.name, c.phone, c.email or '', c._total_orders, c._total_spent, c.created_at,
            ])