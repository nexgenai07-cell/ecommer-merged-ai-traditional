# PATH: apps/orders/urls.py

from django.urls import path
from .views import (
    AdminOrderDetailView,
    CheckoutView,
    OrderListView,
    OrderDetailView,
    OrderCancelView,
    OrderTrackView,
    AdminOrderListView,
    AdminOrderStatusUpdateView,
    AdminOrderReinstateView,
    AdminOrderFilterView,
    )
from .return_views import (
    CreateReturnView, ReturnListView, ReturnDetailView, AdminReturnStatusUpdateView,
)
from .complaint_views import (
    CreateComplaintView, ComplaintDetailView,
    AdminComplaintStatusUpdateView, ComplaintMessageListCreateView,
)

# Customer-facing order URLs (mounted at /api/v1/orders/)
urlpatterns = [
    path('checkout/', CheckoutView.as_view(), name='checkout'),
    path('', OrderListView.as_view(), name='order-list'),
    path('<str:order_number>/', OrderDetailView.as_view(), name='order-detail'),
    path('<str:order_number>/cancel/', OrderCancelView.as_view(), name='order-cancel'),
    path('<str:order_number>/track/', OrderTrackView.as_view(), name='order-track'),
    path('<str:order_number>/return/', CreateReturnView.as_view(), name='order-return-create'),
]

# Admin order URLs (mounted at /api/v1/admin/orders/)
admin_order_urlpatterns = [
    path("", AdminOrderListView.as_view(), name="admin-order-list"),
    path("filter/", AdminOrderFilterView.as_view(), name="admin-order-filter"),
    path("<str:order_number>/", AdminOrderDetailView.as_view(), name="admin-order-detail"),
    path("<str:order_number>/status/", AdminOrderStatusUpdateView.as_view(), name="admin-order-status"),
    path("<str:order_number>/reinstate/", AdminOrderReinstateView.as_view(), name="admin-order-reinstate"),
]

# Returns (mounted at /api/v1/returns/ and /api/v1/admin/returns/)
return_urlpatterns = [
    path('', ReturnListView.as_view(), name='return-list'),
    path('<int:pk>/', ReturnDetailView.as_view(), name='return-detail'),
]

admin_return_urlpatterns = [
    path('<int:pk>/status/', AdminReturnStatusUpdateView.as_view(), name='admin-return-status'),
]

# Complaints (mounted at /api/v1/complaints/ and /api/v1/admin/complaints/)
complaint_urlpatterns = [
    path('', CreateComplaintView.as_view(), name='complaint-create-list'),
    path('<int:pk>/', ComplaintDetailView.as_view(), name='complaint-detail'),
    # NEW (Backend Change Request v2, Part 2 — Item 4): replaces the
    # removed PUT /api/v1/admin/complaints/{id}/respond/. Same path for
    # both customer and admin — permission is enforced inside the view
    # (owning customer OR any admin), matching the spec's "Allowed for
    # the complaint's owning customer OR any admin."
    path('<int:pk>/messages/', ComplaintMessageListCreateView.as_view(), name='complaint-messages'),
]

admin_complaint_urlpatterns = [
    path('<int:pk>/status/', AdminComplaintStatusUpdateView.as_view(), name='admin-complaint-status'),
]