# PATH: apps/orders/customer_views.py

from rest_framework import generics, permissions
from django.db.models import Q
from .models import Customer
from .customer_serializers import CustomerAdminSerializer
from apps.users.permissions import IsAdmin

# Returns a searchable list of all customers for administrators.
class AdminCustomerListView(generics.ListAPIView):
    """
    GET /api/v1/admin/customers/?search=
    Admin-only — list all customers with order count and total spent.

    FIX (Postman testing — 11 Jul 2026): doc (API 101) expects a plain
    array. No pagination_class was set here, so the project's global
    DEFAULT_PAGINATION_CLASS was applying automatically, wrapping the
    response in {count, next, previous, results}. pagination_class =
    None overrides that for this view.
    """
    serializer_class = CustomerAdminSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    pagination_class = None
    
# Fetches all customers and applies search + order-status filters if provided.
    def get_queryset(self):
        qs = Customer.objects.select_related('user').all().order_by('-created_at')
        search = self.request.query_params.get('search')
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(phone__icontains=search) | Q(email__icontains=search))

        # FIX (B57): "Pending" filter tha hi nahi is view mein — is liye
        # kaam nahi kar raha tha. Ab ?status=pending_payment (ya koi bhi
        # valid Order status) se customers ko filter kiya ja sakta hai
        # jinke us status waale order hain. .distinct() isliye taake ek
        # customer ke multiple matching orders hone par woh list mein
        # duplicate na aaye.
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(orders__status=status_filter).distinct()

        return qs

# Returns complete information for a selected customer.
class AdminCustomerDetailView(generics.RetrieveAPIView):
    """GET /api/v1/admin/customers/{id}/"""
    serializer_class = CustomerAdminSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    queryset = Customer.objects.select_related('user').all()