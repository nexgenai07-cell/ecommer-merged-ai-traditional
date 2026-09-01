# PATH: apps/orders/address_urls.py

from django.urls import path
from .address_views import (
    AddressListCreateView,
    AddressDetailView,
    AddressSetDefaultView,
)

# Mounted at /api/v1/addresses/ in core/urls.py
address_urlpatterns = [
    path('', AddressListCreateView.as_view(), name='address-list-create'),
    path('<int:pk>/', AddressDetailView.as_view(), name='address-detail'),
    path('<int:pk>/set-default/', AddressSetDefaultView.as_view(), name='address-set-default'),
]