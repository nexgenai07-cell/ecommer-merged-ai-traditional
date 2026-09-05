# PATH: apps/orders/customer_urls.py

from django.urls import path
from .customer_views import AdminCustomerListView, AdminCustomerDetailView

# mounted at /api/v1/admin/customers/ in core/urls.py
admin_customer_urlpatterns = [
    path('', AdminCustomerListView.as_view(), name='admin-customer-list'),
    path('<int:pk>/', AdminCustomerDetailView.as_view(), name='admin-customer-detail'),
]