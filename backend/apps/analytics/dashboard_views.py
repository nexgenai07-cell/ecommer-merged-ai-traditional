#apps/analytics/dashboard_views.py
import calendar
import datetime as dt
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.cache import cache
from django.db.models import (
    Sum, Count, Min, Max, OuterRef, Subquery, IntegerField, Q, Value, DecimalField, F,
)
from django.db.models.functions import (
    TruncDate,
    TruncWeek,
    TruncMonth,
    TruncYear,
    Coalesce,
    Replace,
)
from django.utils import timezone

from rest_framework import permissions, status, generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError

from apps.orders.models import Order, OrderItem, Customer
from apps.products.models import Product, Discount
from apps.social.models import SocialPost
from apps.returns.models import Return, Complaint
from apps.categories.models import Category
from apps.ai.models import AuditLog
from apps.whatsapp.models import WhatsAppLog
from apps.users.permissions import IsAdmin
from core.pagination import StandardResultsPagination
from core.date_range import get_date_range

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


def filter_orders_by_status(qs, status_param):
    """
    NEW (Sep 2026 - Sales/Revenue Report vs Export mismatch fix): a single
    place that maps the dashboard's status filter to the underlying Order
    query, used by SalesReportView, RevenueReportView, and their matching
    CSV exports (_export_sales / _export_revenue) - so switching the
    status filter on the dashboard and exporting always show the exact
    same records. Previously the report views were hardcoded to "paid
    orders only" with no way to switch, and _export_sales had NO status
    filter at all (exported every order regardless of status), which is
    exactly why the dashboard cards and the downloaded CSV disagreed.

    "sold"      -> Order.REVENUE_STATUSES (confirmed / shipped /
                   out_for_delivery / delivered). This is the default
                   when no status is given, so existing calls keep
                   behaving exactly as before this fix.
    "cancelled" -> status=cancelled orders that were never actually
                   refunded (e.g. cancelled while still pending_payment
                   / on_hold - there was no payment to refund).
    "refunded"  -> status=cancelled orders where payment.status is
                   "refunded" (money was taken, then given back).
    "all"       -> every order, no status filtering at all.
    Any other exact Order.status value (on_hold, pending_payment,
    confirmed, shipped, out_for_delivery, delivered) filters to just
    that status. An unrecognized value falls back to "sold" rather than
    silently returning unfiltered data.
    """
    status_param = (status_param or "sold").lower()

    if status_param == "sold":
        return qs.filter(status__in=Order.REVENUE_STATUSES)
    if status_param == "cancelled":
        return qs.filter(status="cancelled").exclude(payment__status="refunded")
    if status_param == "refunded":
        return qs.filter(status="cancelled", payment__status="refunded")
    if status_param == "all":
        return qs

    valid_statuses = {choice[0] for choice in Order.STATUS_CHOICES}
    if status_param in valid_statuses:
        return qs.filter(status=status_param)

    return qs.filter(status__in=Order.REVENUE_STATUSES)


def format_phone_for_csv(phone):
    """
    NEW (Sep 2026 — Customers CSV export phone number bug): a plain CSV
    write of a local-format Pakistani number (e.g. "03001234567") gets
    reinterpreted by Excel as a plain number the moment the file is
    opened directly (double-click) — Excel drops the leading 0, showing
    "3001234567", which is exactly the "poora number nahi aa raha" bug
    reported. Two things fix this together:
      1. normalize local "0XXXXXXXXXX" to the international
         "+92XXXXXXXXXX" format that was asked for.
      2. wrap it as ="..." — a text-formula Excel recognises when
         opening a CSV directly, so it displays the exact string
         (leading + included) instead of trying to parse it as a number.
    Returns '' unchanged for a blank/missing phone.
    """
    if not phone:
        return ''

    digits = re.sub(r'\D', '', phone)
    if not digits:
        return ''

    if digits.startswith('92'):
        normalized = '+' + digits
    elif digits.startswith('0'):
        normalized = '+92' + digits[1:]
    else:
        normalized = '+92' + digits

    return f'="{normalized}"'


def csv_safe_text(value):
    """
    NEW (19 Sep 2026 - export audit): guards against CSV / spreadsheet
    formula injection. A text value that starts with =, +, - or @ (or a
    tab / carriage return) is executed as a FORMULA by Excel / Google
    Sheets the moment the admin opens the exported file - so anyone who
    can put text into the database (a WhatsApp message they send, the
    name they register with) could run a formula on the admin's machine
    (e.g. =HYPERLINK(...) leaking data, or DDE). Prefixing a single
    quote makes the spreadsheet treat the cell as plain text.

    Only used for free text that outside people control (WhatsApp
    messages, customer names in the WhatsApp exports) - NOT for phone
    numbers, which deliberately use format_phone_for_csv() above.
    """
    if isinstance(value, str) and value and value[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + value
    return value


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


        # FIX (Sep 2026 — Total Spent / Revenue consistency): exclude(status='cancelled')
        # wrongly counted pending_payment and on_hold orders as revenue (money that
        # was never actually confirmed as paid). Switched to an explicit include-list,
        # Order.REVENUE_STATUSES (confirmed / shipped / out_for_delivery / delivered) —
        # same rule used everywhere else (admin customer list, customer's own
        # dashboard, CSV export) so every 'revenue'/'total_spent' number agrees.
        delivered_orders = Order.objects.filter(status__in=Order.REVENUE_STATUSES)


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

        # FIX (Sep 2026 — Sales/Revenue Report vs Export mismatch): status
        # is now a real, explicit filter (?status=sold|cancelled|refunded|
        # all|<exact status>, defaults to "sold") instead of being
        # hardcoded to paid orders only - see filter_orders_by_status().
        qs = filter_orders_by_date(
            filter_orders_by_status(Order.objects.all(), request.query_params.get("status")),
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

        # FIX (Sep 2026 — Sales/Revenue Report vs Export mismatch): status
        # is now a real, explicit filter (?status=sold|cancelled|refunded|
        # all|<exact status>, defaults to "sold") instead of being
        # hardcoded to paid orders only - see filter_orders_by_status().
        qs = filter_orders_by_date(
            filter_orders_by_status(Order.objects.all(), request.query_params.get("status")),
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



class BestSellersView(generics.GenericAPIView):
    """GET /api/v1/analytics/products/best-sellers/?start_date=&end_date=&limit=5&category_id=&ordering=&page=&page_size=

    FIX (Frontend audit, Sep 2026): added category_id (accepts a single
    id or comma-separated ids, same convention as elsewhere in the app)
    and real page/page_size pagination — previously this was hard
    -capped at `limit` (frontend used limit=50) with no way to see
    products ranked 51+. When `page` is passed, the standard
    {count, next, previous, results} shape is returned instead; when
    it's omitted, the old `limit`-sliced plain-array behaviour is kept
    unchanged for existing callers (e.g. dashboard widgets using
    ?limit=5).

    FIX (API 95 — ordering param, Sep 2026): added `ordering` so
    "Top Products by Revenue" can rank by total_revenue instead of
    being limited to whatever the units-sold ranking already let
    through (previously a Rs 120,000 / 2-units product could rank
    ~70th by units and never survive `limit=50`, even though it had
    the highest revenue). Ordering is applied to the queryset BEFORE
    `limit`/pagination via .order_by(), with a deterministic tie-break
    (total_sold desc, then product_id asc) so paginated pages never
    overlap or skip items. Default stays `-total_sold` — the exact
    previous behaviour — so existing callers (e.g. the dashboard
    widget using ?limit=5) are unaffected. An unrecognized value
    returns 400 instead of silently falling back, per the frontend's
    request, so a typo doesn't quietly reorder the page.
    """
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    pagination_class = StandardResultsPagination

    ORDERING_MAP = {
        "-total_sold": ("-total_sold", "product_id"),
        "total_sold": ("total_sold", "product_id"),
        "-total_revenue": ("-total_revenue", "-total_sold", "product_id"),
        "total_revenue": ("total_revenue", "-total_sold", "product_id"),
    }

    def get(self, request):
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        limit = int(request.query_params.get("limit", 5))
        category_id = request.query_params.get("category_id")
        ordering = request.query_params.get("ordering", "-total_sold")

        # NEW: reject an unrecognized ordering value with 400 rather than
        # silently falling back — the frontend explicitly asked for this
        # so a typo'd `ordering` doesn't quietly reorder the widget.
        if ordering not in self.ORDERING_MAP:
            return Response(
                {
                    "error": (
                        f"Invalid ordering '{ordering}'. Allowed values: "
                        f"{', '.join(self.ORDERING_MAP.keys())}"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )


        # FIX (Sep 2026 — Total Spent / Revenue consistency): see DashboardView above.
        qs = OrderItem.objects.filter(order__status__in=Order.REVENUE_STATUSES)


        if start_date:
            qs = qs.filter(order__created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(order__created_at__date__lte=end_date)

        # NEW: category filter — accepts one id or comma-separated ids.
        if category_id:
            category_ids = [v.strip() for v in category_id.split(",") if v.strip()]
            if category_ids:
                qs = qs.filter(product__category_id__in=category_ids)


        qs = (
            qs.values("product_id", "product_name")
              .annotate(
                  total_sold=Sum("quantity"),
                  total_revenue=Sum("total_price"),
              )
              .order_by(*self.ORDERING_MAP[ordering])
        )

        def _serialize(rows):
            return [
                {
                    "product_id": item["product_id"],
                    "name": item["product_name"],   # API docs expect "name"
                    "total_sold": item["total_sold"],
                    "total_revenue": item["total_revenue"],
                }
                for item in rows
            ]

        # NEW: real pagination — only kicks in when the caller actually
        # sends a `page` param, so existing ?limit=N callers (e.g.
        # dashboard widgets) keep getting the exact same plain-array
        # response they always did.
        if "page" in request.query_params:
            page = self.paginate_queryset(qs)
            return self.get_paginated_response(_serialize(page))

        return Response(_serialize(qs[:limit]))


class LowPerformingProductsView(generics.GenericAPIView):
    """GET /api/v1/analytics/products/low-performing/?limit=5&category_id=&page=&page_size= — least sold active products

    FIX (Frontend audit, Sep 2026): same category_id + real pagination
    fix as BestSellersView above — see that docstring for the full
    rationale. The original design wanted a "Category: All" filter here
    and it had to be removed from the UI since the backend had nowhere
    to send it; category_id now exists for that.
    """
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    pagination_class = StandardResultsPagination


    def get(self, request):
        limit = int(request.query_params.get('limit', 5))
        category_id = request.query_params.get('category_id')


        # FIX (Sep 2026 — Total Spent / Revenue consistency): see DashboardView above.
        sold_product_ids = (
            OrderItem.objects.filter(order__status__in=Order.REVENUE_STATUSES)
            .values('product_id')
            .annotate(total_sold=Sum('quantity'))
        )
        sold_map = {row['product_id']: row['total_sold'] for row in sold_product_ids}


        products = Product.objects.filter(
    is_active=True,
    is_delete=False,
        )

        # NEW: category filter — accepts one id or comma-separated ids.
        if category_id:
            category_ids = [v.strip() for v in category_id.split(',') if v.strip()]
            if category_ids:
                products = products.filter(category_id__in=category_ids)

        ranked = sorted(products, key=lambda p: sold_map.get(p.id, 0))

        def _serialize(rows):
            return [
                {
                    'product_id': p.id,
                    'name': p.name,
                    'total_sold': sold_map.get(p.id, 0),
                    'stock': p.stock,
                }
                for p in rows
            ]

        # NEW: real pagination — only kicks in when the caller actually
        # sends a `page` param, so existing ?limit=N callers keep
        # getting the exact same plain-array response they always did.
        if "page" in request.query_params:
            page = self.paginate_queryset(ranked)
            return self.get_paginated_response(_serialize(page))

        return Response(_serialize(ranked[:limit]))



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
    original accepted values are exactly the ones the frontend already
    sends (confirmed, none needed renaming):
        sales, orders, discounts, inventory, returns, complaints,
        social_posts, customers, revenue, products
    A missing or unrecognized 'type' returns 400 with the full accepted
    list, instead of silently exporting orders.

    NEW (16 Sep 2026 — Filtering Fix / frontend request): 4 more types
    added so every admin page can export server-side instead of paging
    through everything client-side and building the CSV in the browser:
        categories, audit_logs, whatsapp_numbers, whatsapp_conversation
    See each _export_<type> method below for its specific params.
    'products' also gained the full Products-page filter set (category,
    price range, stock status, search) it was missing — previously only
    start_date/end_date/type/status were honoured, so this export could
    never match what was actually filtered on screen.

    AUDIT (19 Sep 2026 - export review): the 4 types above and the Products
    filters were checked against the on-screen list endpoints they are
    supposed to match, and these gaps were fixed:
      - start_date/end_date are now validated once, for every type, with
        the same rules as every list page (core/date_range.py): a
        malformed date or start_date after end_date returns 400 instead
        of crashing with a 500 / silently exporting nothing.
      - audit_logs: ?user= accepts the admin's NAME as well as the id,
        exactly like GET /admin/audit-logs/ (17 Sep 2026 fix) - a name
        used to crash the export with a 500.
      - products: same price validation as /products/search/ (400 for
        bad / negative / reversed prices instead of a 500), the
        "in_stock" status alias, case-insensitive in_stock=true/false,
        and ?ordering= (so the CSV rows come in the same order as the
        screen).
      - categories: ?search= and ?ordering= (incl. product_count), same
        as GET /categories/.
      - WhatsApp exports: message text / customer names are made safe
        against spreadsheet formula injection (see csv_safe_text()).

    AUDIT (22 Sep 2026 - export filter gaps): the 7 remaining gap types
    listed in the "CSV Export — Missing/Unsupported Filters" request are
    now fixed — each one accepts exactly the params listed there and
    filters identically to the equivalent on-screen list endpoint:
      - orders: status, search, product, category, ordering (matches
        GET /api/v1/admin/orders/filter/ — AdminOrderFilterView)
      - returns: status, search, ordering (matches GET /api/v1/returns/
        — ReturnListView)
      - complaints: status, priority, search (matches GET
        /api/v1/complaints/ — CreateComplaintView's admin listing)
      - discounts: status, discount_type, search, ordering (matches GET
        /api/v1/discounts/ — DiscountViewSet; sent as discount_type so
        it can't collide with this endpoint's own ?type= dispatch param)
      - inventory: status, category_id, search (matches the Inventory
        Alerts filters on GET /api/v1/products/search/)
      - customers: search, ordering (matches GET
        /api/v1/admin/customers/ — AdminCustomerListView, incl. the
        digit-normalised phone search)
      - social_posts: status, platform, search (matches GET
        /api/v1/social/posts/ — SocialPostViewSet)
    The 7 types already listed as "Already correct" in that request
    (sales, revenue, products, categories, audit_logs,
    whatsapp_numbers, whatsapp_conversation) are untouched.

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
        # NEW (16 Sep 2026 — Filtering Fix):
        'categories', 'audit_logs', 'whatsapp_numbers', 'whatsapp_conversation',
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

        # UPDATED (19 Sep 2026 - export review): validated with the same
        # shared helper every list page uses (core/date_range.py) instead
        # of the raw parse_date_range() - a malformed date used to crash
        # with a 500, and start_date after end_date silently exported an
        # empty file. Returns real date objects (or None when blank).
        start_date, end_date = get_date_range(request.query_params)

        # NEW (16 Sep 2026 — Filtering Fix): whatsapp_conversation exports
        # a single customer's message thread, so phone_number is required.
        if export_type == 'whatsapp_conversation' and not request.query_params.get('phone_number'):
            return Response(
                {'error': "The 'phone_number' query parameter is required for type=whatsapp_conversation."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{export_type}_export.csv"'
        writer = csv.writer(response)

        handler = getattr(self, f'_export_{export_type}')

        # FIX (Sep 2026 — Sales/Revenue Report vs Export mismatch): the
        # export now honours the same ?status= filter as SalesReportView/
        # RevenueReportView (see filter_orders_by_status()), so exporting
        # while the dashboard has a status filter selected downloads
        # exactly what's on screen, not a different unfiltered set.
        if export_type in ('sales', 'revenue'):
            handler(writer, start_date, end_date, request.query_params.get('status'))
        else:
            handler(writer, start_date, end_date)

        return response

    # ---- per-type CSV handlers ----------------------------------------

    def _export_orders(self, writer, start_date, end_date):
        # UPDATED (22 Sep 2026 — export filter gaps): status, search,
        # product, category and ordering now match GET
        # /api/v1/admin/orders/filter/ (Admin — Filter Orders / API 62 /
        # AdminOrderFilterView) exactly, so exporting from the Order
        # Management page always matches what's on screen. Previously
        # only start_date/end_date reached this export — the other 5
        # active filters on that page were silently ignored.
        qs = filter_orders_by_date(Order.objects.all(), start_date, end_date)

        params = self.request.query_params

        status_param = params.get('status')
        if status_param:
            # Same "pending" -> "pending_payment" alias as
            # AdminOrderFilterView.
            if status_param == 'pending':
                status_param = 'pending_payment'
            qs = qs.filter(status=status_param)

        search = params.get('search')
        if search:
            qs = qs.filter(
                Q(order_number__icontains=search) |
                Q(customer__name__icontains=search)
            )

        product = params.get('product')
        if product:
            qs = qs.filter(
                Q(items__product__name__icontains=product) |
                Q(items__product_name__icontains=product)
            )

        category = params.get('category')
        if category:
            qs = qs.filter(items__product__category__name__icontains=category)

        # Joining through items for product/category can duplicate an
        # order row (once per matching item) — de-duplicate, same as
        # AdminOrderFilterView.
        if product or category:
            qs = qs.distinct()

        # FIX (Sep 2026 — Orders sort bug report): sorting now uses the
        # SAME shared whitelist as the admin Orders table
        # (ADMIN_ORDER_ORDERING_MAP in apps/orders/views.py), so the CSV
        # comes out in the exact order shown on screen — including the new
        # customer_name / -customer_name and order_number / -order_number
        # options. Only 4 values were supported here before, and any other
        # sort the page offered was silently ignored. Imported inside the
        # method to avoid a module-load-time dependency between the
        # analytics and orders views.
        from apps.orders.views import apply_admin_order_ordering

        qs = qs.order_by('-created_at')
        qs = apply_admin_order_ordering(qs, params.get('ordering'))

        writer.writerow(['Order Number', 'Customer', 'Total Amount', 'Status', 'Payment Status', 'Cancellation Reason', 'Created At'])
        for order in qs.select_related('customer', 'payment'):
            payment_status = order.payment.status if hasattr(order, 'payment') and order.payment else 'N/A'
            # NEW (Sep 2026 — cancelled orders export): cancellation_reason
            # already exists on the Order model (set when an admin cancels
            # an order — see AdminOrderStatusUpdateView). Blank for any
            # order that was never cancelled, same as a normal empty cell.
            writer.writerow([
                order.order_number, order.customer.name, order.total_amount,
                order.status, payment_status, order.cancellation_reason or '',
                order.created_at,
            ])

    def _export_sales(self, writer, start_date, end_date, status_param=None):
        # FIX (Sep 2026 — Sales/Revenue Report vs Export mismatch): this
        # had NO status filter at all before — every order regardless of
        # status (pending_payment, on_hold, cancelled, everything) was
        # exported, while the Sales Report dashboard cards only ever
        # counted paid orders. That mismatch is exactly the bug reported —
        # dashboard and CSV showing different numbers for the same date
        # range. Now uses filter_orders_by_status() with the same
        # ?status= value the dashboard is filtered to (defaults to "sold",
        # matching SalesReportView's default).
        qs = filter_orders_by_status(Order.objects.all(), status_param)
        qs = filter_orders_by_date(qs, start_date, end_date)
        writer.writerow(['Order Number', 'Customer', 'Items', 'Total Amount', 'Status', 'Created At'])
        for order in qs.select_related('customer').prefetch_related('items'):
            writer.writerow([
                order.order_number, order.customer.name, order.items.count(),
                order.total_amount, order.status, order.created_at,
            ])

    def _export_revenue(self, writer, start_date, end_date, status_param=None):
        # FIX (Sep 2026 — Sales/Revenue Report vs Export mismatch): now
        # uses the same filter_orders_by_status() as RevenueReportView,
        # driven by the same ?status= value the dashboard is filtered to
        # (defaults to "sold" — Order.REVENUE_STATUSES — matching the
        # previous hardcoded behaviour when no filter is selected).
        qs = filter_orders_by_status(Order.objects.all(), status_param)
        qs = filter_orders_by_date(qs, start_date, end_date)
        writer.writerow(['Order Number', 'Customer', 'Total Amount', 'Status', 'Created At'])
        for order in qs.select_related('customer'):
            writer.writerow([
                order.order_number, order.customer.name,
                order.total_amount, order.status, order.created_at,
            ])

    def _export_discounts(self, writer, start_date, end_date):
        # UPDATED (22 Sep 2026 — export filter gaps): status, discount_type,
        # search and ordering now match GET /api/v1/discounts/
        # (DiscountViewSet) exactly — same derived active/inactive/expired
        # status logic, same code search, same ordering whitelist. The
        # frontend's "Type" filter is accepted here as ?discount_type= (the
        # underlying model/list-endpoint field is called "type") so it
        # can't collide with this export's own ?type=discounts dispatch
        # param.
        qs = Discount.objects.filter(is_delete=False)
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)

        params = self.request.query_params

        search = params.get('search')
        if search:
            qs = qs.filter(code__icontains=search)

        discount_type = params.get('discount_type')
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

        ordering_map = {
            'created_at': 'created_at',
            '-created_at': '-created_at',
            'code': 'code',
            '-code': '-code',
            'value': 'value',
            '-value': '-value',
            'end_date': 'end_date',
            '-end_date': '-end_date',
        }
        qs = qs.order_by(ordering_map.get(params.get('ordering'), '-created_at'))

        writer.writerow(['Code', 'Type', 'Value', 'Min Order Amount', 'Start Date', 'End Date', 'Is Active', 'Created At'])
        for d in qs:
            writer.writerow([
                d.code, d.type, d.value, d.min_order_amount or '',
                d.start_date, d.end_date, d.is_active, d.created_at,
            ])

    def _export_inventory(self, writer, start_date, end_date):
        # Inventory is a point-in-time snapshot (current stock levels),
        # so start_date/end_date are intentionally not applied here.
        #
        # FIX (16 Sep 2026 — Filtering Fix / export review): was exporting
        # p.stock, the DEPRECATED field that nothing updates anymore (see
        # Low Stock Products API 38's identical fix) — this CSV was
        # silently exporting stale/wrong stock numbers. Uses
        # available_stock (total_stock - reserved_stock), same as every
        # other stock-reporting endpoint in the app.
        #
        # UPDATED (22 Sep 2026 — export filter gaps): status, category_id
        # and search now match GET /api/v1/products/search/'s Inventory
        # Alerts filters exactly (out_of_stock / low_stock / healthy
        # status logic based on available_stock vs low_stock_threshold,
        # same category_id comma/repeated handling, same name/
        # description/sku/category-name search) — see _export_products
        # below for the identical status-filter logic.
        qs = Product.objects.filter(is_delete=False).select_related('category')

        params = self.request.query_params

        category_id_values = params.getlist('category_id')
        category_ids = []
        for raw in category_id_values:
            category_ids.extend([v.strip() for v in raw.split(',') if v.strip()])
        if category_ids:
            # FIX (22 Sep 2026 — export filter gaps, hardening): a
            # non-numeric category_id used to be handed straight to
            # category_id__in=[...] — Django/the DB driver raises a raw
            # ValueError trying to cast it to an int, which surfaces as
            # an unhandled 500, not a clean error. Same guard as
            # _export_products below / /products/search/ (API 29).
            if not all(v.isdigit() for v in category_ids):
                raise ValidationError({'error': 'category_id must contain only valid ids.'})
            qs = qs.filter(category_id__in=category_ids)

        search = params.get('search')
        if search:
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(description__icontains=search) |
                Q(sku__icontains=search) |
                Q(category__name__icontains=search)
            )

        status_values = params.getlist('status')
        statuses = []
        for raw in status_values:
            statuses.extend([v.strip() for v in raw.split(',') if v.strip()])
        if statuses:
            qs = qs.annotate(_available_stock=F('total_stock') - F('reserved_stock'))
            status_filter = Q()
            if 'out_of_stock' in statuses:
                status_filter |= Q(_available_stock__lte=0)
            if 'low_stock' in statuses:
                status_filter |= Q(_available_stock__gt=0, _available_stock__lte=F('low_stock_threshold'))
            if 'healthy' in statuses or 'in_stock' in statuses:
                status_filter |= Q(_available_stock__gt=F('low_stock_threshold'))
            qs = qs.filter(status_filter)

        writer.writerow(['SKU', 'Name', 'Category', 'Stock', 'Low Stock Threshold', 'Is Active'])
        for p in qs:
            writer.writerow([
                p.sku, p.name, p.category.name if p.category else '',
                p.available_stock, p.low_stock_threshold, p.is_active,
            ])

    def _export_products(self, writer, start_date, end_date):
        # NEW (16 Sep 2026 — Filtering Fix): previously only start_date/
        # end_date were honoured, so this export could never match what
        # was actually filtered on the Products page. Now accepts the
        # same filters as GET /api/v1/products/search/ (API 29):
        # q, category_id, min_price, max_price, in_stock, status, ordering.
        #
        # UPDATED (19 Sep 2026 - export review): brought fully in line
        # with that endpoint - see the AUDIT note in the class docstring.
        qs = Product.objects.filter(is_delete=False).select_related('category')

        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)

        params = self.request.query_params

        q = params.get('q')
        if q:
            qs = qs.filter(
                Q(name__icontains=q) |
                Q(description__icontains=q) |
                Q(sku__icontains=q) |
                Q(category__name__icontains=q)
            )

        category_id_values = params.getlist('category_id')
        category_ids = []
        for raw in category_id_values:
            category_ids.extend([v.strip() for v in raw.split(',') if v.strip()])
        if category_ids:
            # A non-numeric id used to crash the query with a 500.
            if not all(v.isdigit() for v in category_ids):
                raise ValidationError({'error': 'category_id must contain only valid ids.'})
            qs = qs.filter(category_id__in=category_ids)

        # Same validation + messages as /products/search/: bad number,
        # negative, or a reversed range -> 400 (used to be a 500 / an
        # empty file).
        #
        # UPDATED (Sep 2026 - user-friendly price errors): messages are
        # now the same short, plain-language sentences /products/search/
        # returns (see ProductViewSet._parse_price_range), instead of the
        # old technical "min_price cannot be greater than max_price. The
        # range must go from..." string.
        def _parse_price(key):
            raw = params.get(key)
            if raw is None or raw == '':
                return None
            label = 'minimum' if key == 'min_price' else 'maximum'
            try:
                value = Decimal(raw)
            except (InvalidOperation, ValueError):
                raise ValidationError({'error': f'Please enter a valid {label} price.'})
            if not value.is_finite():
                raise ValidationError({'error': f'Please enter a valid {label} price.'})
            if value < 0:
                raise ValidationError({'error': f'{label.capitalize()} price cannot be negative.'})
            return value

        min_price = _parse_price('min_price')
        max_price = _parse_price('max_price')

        if min_price is not None and max_price is not None and min_price > max_price:
            raise ValidationError({
                'error': 'Minimum price cannot be greater than maximum price.'
            })

        if min_price is not None:
            qs = qs.filter(price__gte=min_price)
        if max_price is not None:
            qs = qs.filter(price__lte=max_price)

        in_stock = (params.get('in_stock') or '').lower()
        if in_stock == 'true':
            qs = qs.filter(total_stock__gt=F('reserved_stock'))
        elif in_stock == 'false':
            qs = qs.filter(total_stock__lte=F('reserved_stock'))

        status_values = params.getlist('status')
        statuses = []
        for raw in status_values:
            statuses.extend([v.strip() for v in raw.split(',') if v.strip()])
        if statuses:
            qs = qs.annotate(_available_stock=F('total_stock') - F('reserved_stock'))
            status_filter = Q()
            if 'out_of_stock' in statuses:
                status_filter |= Q(_available_stock__lte=0)
            if 'low_stock' in statuses:
                status_filter |= Q(_available_stock__gt=0, _available_stock__lte=F('low_stock_threshold'))
            # "in_stock" is accepted as an alias of "healthy" by
            # /products/search/ (the frontend uses either name for the
            # "In Stock" tab) - it was missing here, so that tab exported
            # EVERY product instead of only the in-stock ones.
            if 'healthy' in statuses or 'in_stock' in statuses:
                status_filter |= Q(_available_stock__gt=F('low_stock_threshold'))
            if status_filter:
                qs = qs.filter(status_filter)

        # Same whitelist as /products/search/ - anything else is ignored
        # and the model's default ordering (-created_at) is kept.
        ordering = params.get('ordering')
        if ordering in ('created_at', '-created_at', 'price', '-price', 'name', '-name'):
            qs = qs.order_by(ordering)

        writer.writerow(['SKU', 'Name', 'Category', 'Price', 'Stock', 'Is Active', 'Created At'])
        for p in qs:
            writer.writerow([
                # FIX (16 Sep 2026 — Filtering Fix / export review): was
                # p.stock (deprecated, frozen field) — see Low Stock
                # Products API 38 and _export_inventory above for the
                # same fix elsewhere. Uses available_stock instead.
                p.sku, p.name, p.category.name if p.category else '',
                p.price, p.available_stock, p.is_active, p.created_at,
            ])

    def _export_categories(self, writer, start_date, end_date):
        # NEW (16 Sep 2026 — Filtering Fix). Low priority per the
        # frontend's request — Categories is a small, unpaginated list,
        # so the previous behaviour (type not supported at all) already
        # meant the frontend's own client-side export was complete and
        # correct. Added anyway for consistency with the rest of the app.
        qs = Category.objects.filter(is_delete=False)
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)

        # UPDATED (19 Sep 2026 - export review): same ?search= and
        # ?ordering= as GET /api/v1/categories/ (the admin Categories
        # table). Only the dates were honoured before, so searching or
        # sorting on screen and then exporting gave a different list.
        params = self.request.query_params

        search = params.get('search')
        if search:
            qs = qs.filter(name__icontains=search)

        ordering_map = {
            'name': 'name',
            '-name': '-name',
            'created_at': 'created_at',
            '-created_at': '-created_at',
            'product_count': '_product_count',
            '-product_count': '-_product_count',
        }
        # FIX (Categories export mismatch report, Sep 2026): "Product
        # Count" in this CSV counted EVERY product row (including
        # inactive and soft-deleted ones), while the admin Categories
        # table (CategorySerializer.get_product_count) only counts
        # is_active=True products - so the same category showed e.g.
        # "4 items" on screen but 7 in the exported file, and "Most
        # Products" sorted differently in the two places. Counts exactly
        # what the table counts now, and uses the same "name"/"id"
        # tie-breakers as CategoryViewSet.get_queryset() so equal counts
        # come out in the same order as on screen.
        qs = qs.annotate(
            _product_count=Count(
                'products',
                filter=Q(products__is_active=True),
                distinct=True,
            )
        )
        qs = qs.order_by(ordering_map.get(params.get('ordering'), 'name'), 'name', 'id')

        writer.writerow(['Name', 'Is Active', 'Product Count', 'Created At'])
        for c in qs:
            writer.writerow([c.name, c.is_active, c._product_count, c.created_at])

    def _export_audit_logs(self, writer, start_date, end_date):
        # NEW (16 Sep 2026 — Filtering Fix): same filters as
        # GET /api/v1/admin/audit-logs/ (API 82) — entity, user, action,
        # search — so the export matches what's filtered on screen.
        qs = AuditLog.objects.select_related('user').all()
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)

        params = self.request.query_params

        entity = params.get('entity')
        if entity:
            qs = qs.filter(entity=entity)

        # FIX (19 Sep 2026 - export review): same rule as
        # AuditLogListView (17 Sep 2026 production-crash fix). The
        # frontend's User dropdown can send the admin's display NAME
        # instead of the numeric id - filter(user_id="Test Admin 2")
        # crashed this export with a 500. A number is still treated as
        # the id; anything else is matched against the admin's name.
        user_param = params.get('user')
        if user_param:
            if user_param.isdigit():
                qs = qs.filter(user_id=user_param)
            else:
                qs = qs.filter(user__name__iexact=user_param)

        action_param = params.get('action')
        if action_param in ('create', 'update', 'delete'):
            qs = qs.filter(action__startswith=f'{action_param}_')

        search = params.get('search')
        if search:
            qs = qs.filter(Q(action__icontains=search))

        writer.writerow(['User', 'Action', 'Entity', 'Entity ID', 'IP Address', 'Source', 'Created At'])
        for log in qs:
            writer.writerow([
                log.user.email if log.user else 'system', log.action, log.entity,
                log.entity_id or '', log.ip_address or '', log.source, log.created_at,
            ])

    def _export_whatsapp_numbers(self, writer, start_date, end_date):
        # NEW (16 Sep 2026 — Filtering Fix): same search filter and same
        # fields as GET /api/v1/admin/whatsapp/conversations/ (API 116.1)
        # so the export matches what's on screen. See that endpoint for
        # the last-10-digits phone/customer-name matching rationale.
        rows = list(
            WhatsAppLog.objects
            .values('phone_number')
            .annotate(
                last_message_at=Max('created_at'),
                message_count=Count('id'),
            )
            .order_by('-last_message_at')
        )

        def _last10(phone):
            digits = re.sub(r'\D', '', phone or '')
            return digits[-10:] if digits else ''

        customer_names_by_digits = {
            _last10(phone): name
            for phone, name in Customer.objects.exclude(phone__isnull=True)
            .exclude(phone='').values_list('phone', 'name')
        }
        for row in rows:
            row['customer_name'] = customer_names_by_digits.get(_last10(row['phone_number']))

        search = self.request.query_params.get('search', '').strip()
        if search:
            search_lower = search.lower()
            rows = [
                row for row in rows
                if search_lower in row['phone_number'].lower()
                or (row['customer_name'] and search_lower in row['customer_name'].lower())
            ]

        writer.writerow(['Phone Number', 'Customer Name', 'Message Count', 'Last Message At'])
        for row in rows:
            writer.writerow([
                row['phone_number'], csv_safe_text(row['customer_name'] or ''),
                row['message_count'], row['last_message_at'],
            ])

    def _export_whatsapp_conversation(self, writer, start_date, end_date):
        # NEW (16 Sep 2026 — Filtering Fix): full message log for one
        # phone number (required — validated in get() above), for the
        # WhatsApp Chat Panel's export. Low priority per the frontend's
        # request — the whole thread already loads on screen, so the
        # previous client-side export was already complete and correct.
        phone_number = self.request.query_params.get('phone_number')
        qs = WhatsAppLog.objects.filter(phone_number=phone_number).order_by('created_at')
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)
        writer.writerow(['Direction', 'Message', 'Is Admin', 'Created At'])
        for log in qs:
            # csv_safe_text: the message text is written by whoever
            # messaged the bot - never trust it as a spreadsheet cell.
            writer.writerow([log.direction, csv_safe_text(log.message), log.is_admin, log.created_at])

    def _export_returns(self, writer, start_date, end_date):
        # UPDATED (22 Sep 2026 — export filter gaps): status, search and
        # ordering now match GET /api/v1/returns/ (ReturnListView)
        # exactly — same "requested" -> "pending" alias, same search
        # fields (order number, reason, customer name, RET-<id>
        # reference), same ordering whitelist.
        qs = Return.objects.select_related('order', 'customer')
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)

        params = self.request.query_params

        status_param = params.get('status')
        if status_param == 'requested':
            status_param = 'pending'
        if status_param in ('pending', 'approved', 'rejected'):
            qs = qs.filter(status=status_param)

        search = (params.get('search') or '').strip()
        if search:
            search_filter = (
                Q(order__order_number__icontains=search) |
                Q(reason__icontains=search) |
                Q(customer__name__icontains=search)
            )
            # FIX (Sep 2026 — ID search bug report): same ID spellings as
            # the Returns table ("#RET-20", "RET 20", "#20", "20") — see
            # reference_id_from_search() in apps/orders/return_views.py.
            # Imported here to avoid a module-load-time dependency.
            from apps.orders.return_views import reference_id_from_search
            ref = reference_id_from_search(search, 'ret')
            if ref:
                ref_id, explicit = ref
                if explicit:
                    search_filter = Q(id=ref_id)
                else:
                    search_filter |= Q(id=ref_id)
            qs = qs.filter(search_filter)

        ordering_map = {
            'created_at': 'created_at',
            '-created_at': '-created_at',
            'customer_name': 'customer__name',
            '-customer_name': '-customer__name',
        }
        qs = qs.order_by(ordering_map.get(params.get('ordering'), '-created_at'))

        writer.writerow(['Order Number', 'Customer', 'Reason', 'Status', 'Created At', 'Resolved At'])
        for r in qs:
            writer.writerow([
                r.order.order_number, r.customer.name if r.customer else '',
                r.reason, r.status, r.created_at, r.resolved_at or '',
            ])

    def _export_complaints(self, writer, start_date, end_date):
        # UPDATED (22 Sep 2026 — export filter gaps): status, priority and
        # search now match GET /api/v1/complaints/ (CreateComplaintView's
        # admin listing) exactly — same accepted status/priority values,
        # same search fields (message text, CMP-<id> reference). The page
        # itself has no date filter yet, so start_date/end_date are left
        # as-is below (harmless if the page never sends them).
        qs = Complaint.objects.select_related('customer', 'order')
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)

        params = self.request.query_params

        status_param = params.get('status')
        if status_param in ('open', 'in_progress', 'resolved', 'closed'):
            qs = qs.filter(status=status_param)

        priority_param = params.get('priority')
        if priority_param in ('normal', 'urgent'):
            qs = qs.filter(priority=priority_param)

        search = (params.get('search') or '').strip()
        if search:
            search_filter = Q(message__icontains=search)
            # FIX (Sep 2026 — ID search bug report): same ID spellings as
            # the Complaints table ("#CMP-51", "CMP 51", "#51", "51").
            from apps.orders.return_views import reference_id_from_search
            ref = reference_id_from_search(search, 'cmp')
            if ref:
                ref_id, explicit = ref
                if explicit:
                    search_filter = Q(id=ref_id)
                else:
                    search_filter |= Q(id=ref_id)
            qs = qs.filter(search_filter)

        writer.writerow(['ID', 'Customer', 'Order Number', 'Type', 'Status', 'Priority', 'Created At'])
        for c in qs:
            writer.writerow([
                c.id, c.customer.name, c.order.order_number if c.order else '',
                c.type, c.status, c.priority, c.created_at,
            ])

    def _export_social_posts(self, writer, start_date, end_date):
        # UPDATED (22 Sep 2026 — export filter gaps): status, platform and
        # search now match GET /api/v1/social/posts/ (SocialPostViewSet)
        # exactly — same caption/hashtags search. Status and platform are
        # straightforward equality filters against the model's own
        # STATUS_CHOICES / PLATFORM_CHOICES, the same fields the Social
        # Posts page's own tabs already write to.
        qs = SocialPost.objects.all()
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)

        params = self.request.query_params

        status_param = params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)

        platform = params.get('platform')
        if platform:
            qs = qs.filter(platform=platform)

        search = params.get('search')
        if search:
            qs = qs.filter(Q(caption__icontains=search) | Q(hashtags__icontains=search))

        writer.writerow(['ID', 'Platform', 'Caption', 'Hashtags', 'Status', 'Created At'])
        for p in qs:
            writer.writerow([
                p.id, p.platform, p.caption, p.hashtags, p.status, p.created_at,
            ])

    def _export_customers(self, writer, start_date, end_date):
        # Same "exclude cancelled + pending_payment" total_orders /
        # total_spent logic as AdminCustomerListView (A2), for
        # consistency between the admin customers list and this export.
        #
        # UPDATED (22 Sep 2026 — export filter gaps): search and ordering
        # now match GET /api/v1/admin/customers/ (AdminCustomerListView)
        # exactly, including the digit-normalised phone search and the
        # total_orders/total_spent sort options (which reuse the same
        # annotations already computed below, so the CSV order matches
        # the screen order exactly).
        qs = Customer.objects.all()
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)

        # FIX (Sep 2026 — Total Spent / Revenue consistency): see DashboardView above.
        qs = qs.annotate(
            _total_orders=Count(
                'orders', filter=Q(orders__status__in=Order.REVENUE_STATUSES), distinct=True,
            ),
            _total_spent=Coalesce(
                Sum('orders__total_amount', filter=Q(orders__status__in=Order.REVENUE_STATUSES)),
                Value(0), output_field=DecimalField(),
            ),
        )

        params = self.request.query_params

        search = params.get('search')
        if search:
            search_filter = Q(name__icontains=search) | Q(email__icontains=search) | Q(phone__icontains=search)

            # Same phone-digit normalisation as AdminCustomerListView, so
            # a plain-digit WhatsApp-style search still matches a phone
            # stored with punctuation.
            search_digits = re.sub(r'[\s\-()+]', '', search)
            if len(re.sub(r'\D', '', search_digits)) >= 6:
                qs = qs.annotate(
                    _phone_digits=Replace(
                        Replace(
                            Replace(
                                Replace(
                                    Replace('phone', Value('+'), Value('')),
                                    Value(' '), Value(''),
                                ),
                                Value('-'), Value(''),
                            ),
                            Value('('), Value(''),
                        ),
                        Value(')'), Value(''),
                    )
                )
                search_filter |= Q(_phone_digits__icontains=search_digits)

            qs = qs.filter(search_filter)

        ordering_map = {
            'created_at': 'created_at',
            '-created_at': '-created_at',
            'total_orders': '_total_orders',
            '-total_orders': '-_total_orders',
            'total_spent': '_total_spent',
            '-total_spent': '-_total_spent',
        }
        qs = qs.order_by(ordering_map.get(params.get('ordering'), '-created_at'))

        writer.writerow(['Name', 'Phone', 'Email', 'Total Orders', 'Total Spent', 'Created At'])
        for c in qs:
            writer.writerow([
                c.name, format_phone_for_csv(c.phone), c.email or '', c._total_orders, c._total_spent, c.created_at,
            ])