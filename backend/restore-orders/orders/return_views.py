# PATH: apps/orders/return_views.py
# (placed inside apps/orders to access Order model easily; imported by orders/urls.py)

from django.utils import timezone
from rest_framework import status, permissions, generics
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.returns.models import Return
from apps.returns.serializers import ReturnSerializer, CreateReturnSerializer, AdminReturnStatusSerializer
from .models import Order
from apps.users.permissions import IsAdmin
from core.pagination import StandardResultsPagination

# Allows customers to submit a return request for an order.
# Only logged-in users can request returns.
class CreateReturnView(APIView):
    """
    POST /api/v1/orders/{order_number}/return/
    Customer can only request a return on a DELIVERED order.
    """
    permission_classes = [permissions.IsAuthenticated]

# Finds the customer's order using the order number.
# Returns are only allowed after the order has been delivered.
    def post(self, request, order_number):
        try:
            order = Order.objects.get(order_number=order_number, customer__user=request.user)
        except Order.DoesNotExist:
            return Response({'error': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)

        if order.status != 'delivered':
            return Response(
                {'error': 'Only delivered orders are eligible for return.'},
                status=status.HTTP_400_BAD_REQUEST
            )

# Prevents creating multiple return requests for the same order.
        if Return.objects.filter(
               order=order,
               status__in=["pending", "approved"]
            ).exists():
            return Response({'error': 'A return request already exists for this order.'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = CreateReturnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

# Creates a new return request in the database.
        return_request = Return.objects.create(
            order=order,
            customer=order.customer,
            reason=serializer.validated_data['reason'],
            status='pending',
        )

        return Response(ReturnSerializer(return_request).data, status=status.HTTP_201_CREATED)


class ReturnListView(generics.ListAPIView):
    """
    GET /api/v1/returns/ — customer sees own, admin sees all

    FIX (Postman testing — 09 Jul 2026): doc (API 61) expects
    {count, next, previous, results}. pagination_class wasn't attached
    here before, so it fell back to no pagination at all / an
    incomplete shape. Now explicitly attached.
    """
    serializer_class = ReturnSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsPagination

    def get_queryset(self):
        user = self.request.user
        if user.role == 'admin':
            return Return.objects.all().order_by('-created_at')
        return Return.objects.filter(customer__user=user).order_by('-created_at')


class ReturnDetailView(generics.RetrieveAPIView):
    """GET /api/v1/returns/{id}/"""
    serializer_class = ReturnSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'admin':
            return Return.objects.all()
        return Return.objects.filter(customer__user=user)


class AdminReturnStatusUpdateView(APIView):
    """
    PUT /api/v1/admin/returns/{id}/status/

    FIX (Postman testing — 09 Jul 2026): doc (API 63) expects
    {"message": "Return status updated.", "status": "approved"} — the
    full ReturnSerializer(return_request).data object was being
    returned before, which doesn't match. Now returns only the
    documented message + status.
    """
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def put(self, request, pk):
        try:
            return_request = Return.objects.get(id=pk)
        except Return.DoesNotExist:
            return Response({'error': 'Return request not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = AdminReturnStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return_request.status = serializer.validated_data['status']
        return_request.resolved_at = timezone.now()
        return_request.save()

        return Response({
            'message': 'Return status updated.',
            'status': return_request.status,
        })