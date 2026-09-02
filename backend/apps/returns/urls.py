# PATH: apps/returns/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import ComplaintViewSet, AdminComplaintStatusViewSet

router = DefaultRouter()
router.register(r'complaints', ComplaintViewSet, basename='complaint')
router.register(r'admin/complaints', AdminComplaintStatusViewSet, basename='admin-complaint')

urlpatterns = [
    path('api/v1/', include(router.urls)),
]

# ============================================================
# DEPRECATED ENDPOINT (REMOVED):
# PUT /api/v1/admin/complaints/{id}/respond/
# This endpoint auto-set status to resolved, which is the bug.
# Use these instead:
#   POST /api/v1/complaints/{id}/messages/  - add message
#   PUT /api/v1/admin/complaints/{id}/status/ - update status
# ============================================================