#apps/analytics/dashboard_views.py
import calendar
import datetime as dt
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import Sum, Count, Min, Max, OuterRef, Subquery, IntegerField
from django.db.models.functions import (
    TruncDate,
    TruncWeek,
    TruncMonth,
    TruncYear,
    Coalesce,
)
from django.utils import timezone

from rest_framework import permissions
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.orders.models import Order, OrderItem, Customer
from apps.products.models import Product
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


        pending_orders = Order.objects.filter(status='pending').count()
        low_stock_products = Product.objects.filter(
    is_active=True,
    is_delete=False,
).count()

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
    """GET /api/v1/analytics/inventory/alerts/ — active products at/below their low stock threshold"""
    permission_classes = [permissions.IsAuthenticated, IsAdmin]


    def get(self, request):
        products = Product.objects.filter(
    is_active=True,
    is_delete=False,
    )
        alerts = [
            {'product_id': p.id, 'name': p.name, 'stock': p.stock, 'threshold': p.low_stock_threshold}
            for p in products if p.stock <= p.low_stock_threshold
        ]
        return Response(alerts)



class AnalyticsExportView(APIView):
    """
    GET /api/v1/analytics/export/?start_date=&end_date=
    Returns a CSV file of orders within the date range.
    """
    permission_classes = [permissions.IsAuthenticated, IsAdmin]


    def get(self, request):
        import csv
        from django.http import HttpResponse


        start_date, end_date, _ = parse_date_range(request)
        qs = filter_orders_by_date(Order.objects.all(), start_date, end_date)


        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="orders_export.csv"'


        writer = csv.writer(response)
        writer.writerow(['Order Number', 'Customer', 'Total Amount', 'Status', 'Payment Status', 'Created At'])


        for order in qs.select_related('customer', 'payment'):
            payment_status = order.payment.status if hasattr(order, 'payment') and order.payment else 'N/A'
            writer.writerow([
                order.order_number, order.customer.name, order.total_amount,
                order.status, payment_status, order.created_at,
            ])


        return response