import os
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static

from apps.cart.urls import wishlist_urlpatterns
from apps.orders.urls import (
    admin_order_urlpatterns, return_urlpatterns, admin_return_urlpatterns,
    complaint_urlpatterns, admin_complaint_urlpatterns,
)
from apps.orders.customer_urls import admin_customer_urlpatterns
from apps.ai.urls import audit_log_urlpatterns
from apps.whatsapp.admin_urls import admin_whatsapp_urlpatterns

urlpatterns = [
    path('admin/', admin.site.urls), #gives all urls from users

    # Module 1
    path('api/v1/auth/', include('apps.users.urls')),
    path('api/v1/stores/', include('apps.stores.urls')),

    # Module 2
    path('api/v1/categories/', include('apps.categories.urls')),
    path('api/v1/products/', include('apps.products.urls')),
    path('api/v1/discounts/', include('apps.products.discount_urls')),

    # Module 3 — Cart, Wishlist, Orders, Returns, Complaints, Customers
    path('api/v1/cart/', include('apps.cart.urls')),
    path('api/v1/wishlist/', include((wishlist_urlpatterns, 'wishlist'))),
    path('api/v1/orders/', include('apps.orders.urls')),
    path('api/v1/admin/orders/', include((admin_order_urlpatterns, 'admin-orders'))),
    path('api/v1/returns/', include((return_urlpatterns, 'returns'))),
    path('api/v1/admin/returns/', include((admin_return_urlpatterns, 'admin-returns'))),
    path('api/v1/complaints/', include((complaint_urlpatterns, 'complaints'))),
    path('api/v1/admin/complaints/', include((admin_complaint_urlpatterns, 'admin-complaints'))),
    path('api/v1/admin/customers/', include((admin_customer_urlpatterns, 'admin-customers'))),
    path("api/v1/payments/", include("apps.payments.urls")),

    # Notifications
    path('api/v1/notifications/', include('apps.notifications.urls')),

    # AI — chat sessions/messages + audit log (WebSocket is in core/asgi.py, not here)
    path('api/v1/chat/', include('apps.ai.urls')),
    path('api/v1/admin/audit-logs/', include((audit_log_urlpatterns, 'audit-logs'))),

    # Analytics
    path('api/v1/analytics/', include('apps.analytics.urls')),

    # Social
    path('api/v1/social/', include('apps.social.urls')),

    # WhatsApp
    path('api/v1/whatsapp/', include('apps.whatsapp.urls')),
    path('api/v1/admin/whatsapp/', include((admin_whatsapp_urlpatterns, 'admin-whatsapp'))),
]

# Serve uploaded media files during development only (Cloudinary handles it in production)
if settings.DEBUG and not os.getenv("CLOUDINARY_CLOUD_NAME"):
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler404 = 'core.views.custom_404'