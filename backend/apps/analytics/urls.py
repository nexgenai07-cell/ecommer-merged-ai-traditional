# PATH: apps/analytics/urls.py

from django.urls import path
from .views import TrackBehaviorView, CustomerBehaviorListView
from .dashboard_views import (
    DashboardView, SalesReportView, RevenueReportView, OrdersAnalyticsView,
    BestSellersView, LowPerformingProductsView, CustomerGrowthView,
    InventoryAlertsView, AnalyticsExportView, ProfitReportView,
)

# Note: included under 'api/v1/analytics/' in core/urls.py
urlpatterns = [
    # Customer behavior tracking (used by frontend + AI tools)
    path('track/', TrackBehaviorView.as_view(), name='track-behavior'),
    path('behavior/', CustomerBehaviorListView.as_view(), name='behavior-list'),

    # Admin dashboard & reports (M7)
    path('dashboard/', DashboardView.as_view(), name='analytics-dashboard'),
    path('sales/', SalesReportView.as_view(), name='analytics-sales'),
    path('revenue/', RevenueReportView.as_view(), name='analytics-revenue'),
    # NEW (Sep 2026 — profit/markup/margin reporting)
    path('profit/', ProfitReportView.as_view(), name='analytics-profit'),
    path('orders/', OrdersAnalyticsView.as_view(), name='analytics-orders'),
    path('products/best-sellers/', BestSellersView.as_view(), name='analytics-best-sellers'),
    path('products/low-performing/', LowPerformingProductsView.as_view(), name='analytics-low-performing'),
    path('customers/growth/', CustomerGrowthView.as_view(), name='analytics-customer-growth'),
    path('inventory/alerts/', InventoryAlertsView.as_view(), name='analytics-inventory-alerts'),
    path('export/', AnalyticsExportView.as_view(), name='analytics-export'),
]