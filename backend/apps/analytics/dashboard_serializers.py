# PATH: apps/analytics/dashboard_serializers.py

from rest_framework import serializers


class DashboardSerializer(serializers.Serializer):
    """Plain serializer — just documents the response shape, data is built in the view"""
    total_revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_orders = serializers.IntegerField()
    total_customers = serializers.IntegerField()
    total_products = serializers.IntegerField()
    revenue_growth = serializers.CharField()
    orders_growth = serializers.CharField()
    pending_orders = serializers.IntegerField()
    low_stock_products = serializers.IntegerField()
    today_revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    today_orders = serializers.IntegerField()
    # NEW (Sep 2026 — review moderation queue)
    pending_reviews = serializers.IntegerField()
    # NEW (Sep 2026 — profit/markup/margin concepts)
    total_cost = serializers.DecimalField(max_digits=14, decimal_places=2)
    gross_profit = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_markup = serializers.DecimalField(max_digits=14, decimal_places=2)
    markup_percent = serializers.FloatField(allow_null=True)
    profit_percent = serializers.FloatField(allow_null=True)
    profit_margin_percent = serializers.FloatField(allow_null=True)


# NEW (Sep 2026 — profit/markup/margin concepts): documents the shape
# shared by ProfitReportView, and the per-period rows / `summary` of
# SalesReportView and RevenueReportView (all built by
# build_concept_metrics() in dashboard_views.py) — data is built in the view.
class ConceptMetricsSerializer(serializers.Serializer):
    units_sold = serializers.IntegerField()
    avg_selling_price_per_unit = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True)
    avg_cost_per_unit = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True)
    markup_per_unit = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True)
    total_revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_cost = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_markup = serializers.DecimalField(max_digits=14, decimal_places=2)
    gross_profit = serializers.DecimalField(max_digits=14, decimal_places=2)
    markup_percent = serializers.FloatField(allow_null=True)
    profit_percent = serializers.FloatField(allow_null=True)
    profit_margin_percent = serializers.FloatField(allow_null=True)
    items_missing_cost = serializers.IntegerField()


class ProfitReportBucketSerializer(ConceptMetricsSerializer):
    date = serializers.CharField()
    total_orders = serializers.IntegerField()
    revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    cost = serializers.DecimalField(max_digits=14, decimal_places=2)


class ProfitReportSummarySerializer(ConceptMetricsSerializer):
    total_orders = serializers.IntegerField()
    discounts_given = serializers.DecimalField(max_digits=14, decimal_places=2)
    shipping_collected = serializers.DecimalField(max_digits=14, decimal_places=2)


class ProfitReportSerializer(serializers.Serializer):
    period = serializers.CharField()
    summary = ProfitReportSummarySerializer()
    data = ProfitReportBucketSerializer(many=True)