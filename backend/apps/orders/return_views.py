# PATH: apps/orders/return_views.py
# (placed inside apps/orders to access Order model easily; imported by orders/urls.py)

from django.utils import timezone
from django.db.models import Q
from rest_framework import status, permissions, generics
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.returns.models import Return
from apps.returns.serializers import ReturnSerializer, CreateReturnSerializer, AdminReturnStatusSerializer
from .models import Order
# FIX (B43): IsCustomer import ki gayi taake return-request endpoint bhi
# admin login se accessible na ho.
from apps.users.permissions import IsAdmin, IsCustomer
from core.pagination import StandardResultsPagination
from apps.notifications.utils import create_notification

# Allows customers to submit a return request for an order.
# Only logged-in customers can request returns.
class CreateReturnView(APIView):
    """
    POST /api/v1/orders/{order_number}/return/
    Customer can only request a return on a DELIVERED order.
    """
    # FIX (B43): customer-only.
    permission_classes = [permissions.IsAuthenticated, IsCustomer]

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
        # FIX (B55): "pending" is not a valid Return status — the model's
        # STATUS_CHOICES only has "requested" (default), "approved",
        # "rejected", "completed". This meant the duplicate-check below
        # never matched anything real, AND every new return was being
        # saved with an invalid status (see fix below) that the admin
        # returns page couldn't recognize/filter on correctly.
        if Return.objects.filter(
               order=order,
               status__in=["requested", "approved"]
            ).exists():
            return Response({'error': 'A return request already exists for this order.'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = CreateReturnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

# Creates a new return request in the database.
        # FIX (B55): status='pending' -> 'requested' (matches Return.STATUS_CHOICES).
        return_request = Return.objects.create(
            order=order,
            customer=order.customer,
            reason=serializer.validated_data['reason'],
            status='requested',
        )

        return Response(ReturnSerializer(return_request).data, status=status.HTTP_201_CREATED)


class ReturnListView(generics.ListAPIView):
    """
    GET /api/v1/returns/ — customer sees own, admin sees all

    FIX (Postman testing — 09 Jul 2026): doc (API 61) expects
    {count, next, previous, results}. pagination_class wasn't attached
    here before, so it fell back to no pagination at all / an
    incomplete shape. Now explicitly attached.

    FIX (D1): 'ordering=created_at' / 'ordering=-created_at' now works
    (default stays -created_at, same as before, when 'ordering' is
    absent or not one of these two values).

    NEW (Follow-up v9, item 1 — CRITICAL): 'status', 'search',
    'start_date', 'end_date' ab sab combine ho kar filter karte hain.
    Pehle sirf 'ordering' hi read hota tha — baaki 4 params silently
    ignore ho rahe thay, isliye admin Returns page ko poori history
    download kar k khud browser mein filter/search/date-range/sort
    karna par raha tha. Same convention use ki hai jo pehle se
    AdminOrderFilterView (orders/views.py) aur CreateComplaintView
    (complaint_views.py) mein implement ho chuki hai — Returns endpoint
    original v7 audit mein miss ho gaya tha, ab close kar diya.

    NOTE for frontend: Return.STATUS_CHOICES mein "pending" naam ki
    koi value nahi hai — asal values requested / approved / rejected /
    completed hain. Naya return CreateReturnView mein status="requested"
    ke sath banta hai, "pending" ke sath nahi. Agar ?status=pending
    bheja gaya to filter theek kaam karega lekin hamesha 0 results
    dega kyunke DB mein wo value kabhi exist hi nahi karti.
    """
    serializer_class = ReturnSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsPagination

    def get_queryset(self):
        user = self.request.user
        if user.role == 'admin':
            qs = Return.objects.all()
        else:
            qs = Return.objects.filter(customer__user=user)

        params = self.request.query_params

        # Status filter — exact match (e.g. ?status=requested)
        status_param = params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)

        # Date filters — return REQUEST date range (Return.created_at pe,
        # order ki date pe nahi). start_date/end_date dono optional hain
        # aur independently kaam karte hain (sirf ek bhi diya ja sakta h).
        start_date = params.get('start_date')
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)

        end_date = params.get('end_date')
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)

        # Search — order number ("ORD-2026" jaisa doc ka example) aur
        # return reason text, dono pe icontains match. Complaints ke
        # search se yahan is liye simpler rakha (regex/ID-match nahi),
        # kyunke Return ka koi alag reference-number format nahi hai —
        # frontend order_number se hi search karta hai.
        search = params.get('search')
        if search:
            qs = qs.filter(
                Q(order__order_number__icontains=search) |
                Q(reason__icontains=search)
            )

        # 'ordering' — sirf whitelist ki gayi 2 values accept, warna
        # invalid value pe DB error na aaye (jaisa AdminOrderFilterView
        # mein already whitelist pattern hai).
        ordering = params.get('ordering')
        if ordering in ('created_at', '-created_at'):
            return qs.order_by(ordering)
        return qs.order_by('-created_at')


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

        # NEW (Cross-check, Sep 2026 — PDF Part 2 Item 3): "return status
        # change" is one of the three automatic notification triggers the
        # spec explicitly names ("order status change, return status
        # change, complaint message"), but no notification was ever being
        # sent here at all — the customer had no way of knowing their
        # return was approved/rejected/completed, and there was nothing
        # for reference_type="return" to attach to anywhere in the app.
        # reference_id is the Return's own id (per spec: "the
        # order_number / return id / complaint id as a string"), not the
        # order_number, to distinguish it from an order notification.
        status_messages = {
            "approved": f"Your return request for order #{return_request.order.order_number} has been approved.",
            "rejected": f"Your return request for order #{return_request.order.order_number} has been rejected.",
            "completed": f"Your return for order #{return_request.order.order_number} has been completed.",
        }
        create_notification(
            user=return_request.customer.user if return_request.customer else return_request.order.customer.user,
            store=return_request.order.store,
            title="Return Status Updated",
            message=status_messages.get(
                return_request.status,
                f"Your return request for order #{return_request.order.order_number} has been updated.",
            ),
            notification_type="order",
            reference_type="return",
            reference_id=return_request.id,
        )

        return Response({
            'message': 'Return status updated.',
            'status': return_request.status,
        })