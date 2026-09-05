from django.urls import path

from .views import (
    AdminOrderDetailView,
    CheckoutView,
    CheckoutPrefillView,
    SaveAddressView,
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
    CreateReturnView,
    ReturnListView,
    ReturnDetailView,
    AdminReturnStatusUpdateView,
)
from .complaint_views import (
    CreateComplaintView,
    ComplaintDetailView,
    ComplaintMessageView,
    AdminComplaintStatusUpdateView,
    AdminComplaintRespondView,
)


# Mount at /api/v1/orders/
urlpatterns = [
    path("checkout/", CheckoutView.as_view(), name="checkout"),
    path(
        "checkout/prefill/",
        CheckoutPrefillView.as_view(),
        name="checkout-prefill",
    ),
    path(
        "save-address/",
        SaveAddressView.as_view(),
        name="save-address",
    ),
    path("", OrderListView.as_view(), name="order-list"),
    path(
        "<str:order_number>/",
        OrderDetailView.as_view(),
        name="order-detail",
    ),
    path(
        "<str:order_number>/cancel/",
        OrderCancelView.as_view(),
        name="order-cancel",
    ),
    path(
        "<str:order_number>/track/",
        OrderTrackView.as_view(),
        name="order-track",
    ),
    path(
        "<str:order_number>/return/",
        CreateReturnView.as_view(),
        name="order-return-create",
    ),
]


# Mount at /api/v1/admin/orders/
admin_order_urlpatterns = [
    path("", AdminOrderListView.as_view(), name="admin-order-list"),
    path(
        "filter/",
        AdminOrderFilterView.as_view(),
        name="admin-order-filter",
    ),
    path(
        "<str:order_number>/",
        AdminOrderDetailView.as_view(),
        name="admin-order-detail",
    ),
    path(
        "<str:order_number>/status/",
        AdminOrderStatusUpdateView.as_view(),
        name="admin-order-status",
    ),
    path(
        "<str:order_number>/reinstate/",
        AdminOrderReinstateView.as_view(),
        name="admin-order-reinstate",
    ),
]


# Mount at /api/v1/returns/
return_urlpatterns = [
    path("", ReturnListView.as_view(), name="return-list"),
    path("<int:pk>/", ReturnDetailView.as_view(), name="return-detail"),
]


# Mount at /api/v1/admin/returns/
admin_return_urlpatterns = [
    path(
        "<int:pk>/status/",
        AdminReturnStatusUpdateView.as_view(),
        name="admin-return-status",
    ),
]


# Mount at /api/v1/complaints/
complaint_urlpatterns = [
    path("", CreateComplaintView.as_view(), name="complaint-create-list"),
    path(
        "<int:pk>/",
        ComplaintDetailView.as_view(),
        name="complaint-detail",
    ),
    path(
        "<int:pk>/messages/",
        ComplaintMessageView.as_view(),
        name="complaint-message-create",
    ),
]


# Mount at /api/v1/admin/complaints/
admin_complaint_urlpatterns = [
    path(
        "<int:pk>/status/",
        AdminComplaintStatusUpdateView.as_view(),
        name="admin-complaint-status",
    ),
    path(
        "<int:pk>/respond/",
        AdminComplaintRespondView.as_view(),
        name="admin-complaint-respond",
    ),
]