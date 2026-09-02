# PATH: apps/returns/views.py

from django.db.models import Q
from rest_framework import viewsets, permissions, status, mixins
from rest_framework.decorators import action
from rest_framework.response import Response

from core.pagination import StandardResultsPagination
from apps.users.permissions import IsAdmin
from apps.notifications.models import Notification
from apps.notifications.serializers import NotificationSerializer
from apps.stores.models import Store
from .models import Complaint, ComplaintMessage
from .serializers import (
    ComplaintSerializer,
    ComplaintDetailSerializer,
    ComplaintMessageSerializer,
    ComplaintMessageCreateSerializer,
    ComplaintStatusUpdateSerializer,
)


class ComplaintViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    ViewSet for complaints.

    GET    /api/v1/complaints/                    - List complaints (admin sees all, customer sees own)
    GET    /api/v1/complaints/{id}/               - Get complaint detail with messages
    GET    /api/v1/complaints/{id}/messages/      - Get full message thread
    POST   /api/v1/complaints/{id}/messages/      - Add a message to complaint
    PUT    /api/v1/admin/complaints/{id}/status/  - Admin only: update status

    NOTE: respond/ endpoint is DEPRECATED and REMOVED per PDF Part 2 Item 4
    """

    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsPagination

    def get_serializer_class(self):
        if self.action == "retrieve":
            return ComplaintDetailSerializer
        return ComplaintSerializer

    def get_queryset(self):
        """Admin sees all complaints; customer sees only their own"""
        user = self.request.user
        queryset = Complaint.objects.all()

        if not user.is_staff:
            # Customer: only their own complaints
            # Get customer profile for this user
            from apps.orders.models import Customer
            try:
                customer = Customer.objects.get(user=user)
                queryset = queryset.filter(customer=customer)
            except Customer.DoesNotExist:
                queryset = queryset.none()

        return queryset.order_by("-created_at")

    def retrieve(self, request, *args, **kwargs):
        """Get complaint with full message thread"""
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @action(detail=True, methods=["get"], url_path="messages")
    def get_messages(self, request, pk=None):
        """
        GET /api/v1/complaints/{id}/messages/
        Full thread, chronological, both roles.
        """
        complaint = self.get_object()
        messages = complaint.messages.all().order_by("created_at")
        serializer = ComplaintMessageSerializer(messages, many=True)
        return Response({"results": serializer.data})

    @action(detail=True, methods=["post"], url_path="messages")
    def post_message(self, request, pk=None):
        """
        POST /api/v1/complaints/{id}/messages/
        Request: {"message": "string"}
        Allowed for complaint's owning customer OR any admin.
        Posting a message NEVER changes status.
        """
        complaint = self.get_object()
        user = request.user

        # Check permissions: customer who owns complaint OR any admin
        is_admin = user.is_staff
        is_owner = False

        if not is_admin:
            from apps.orders.models import Customer
            try:
                customer = Customer.objects.get(user=user)
                is_owner = (complaint.customer == customer)
            except Customer.DoesNotExist:
                is_owner = False

        if not is_admin and not is_owner:
            return Response(
                {"error": "You do not have permission to post to this complaint."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Validate input
        serializer = ComplaintMessageCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Determine sender
        sender = "admin" if is_admin else "customer"
        sender_user = user if is_admin else None

        # Create message
        message = ComplaintMessage.objects.create(
            complaint=complaint,
            sender=sender,
            sender_user=sender_user,
            message=serializer.validated_data["message"],
        )

        # ============================================================
        # Create notification for the other party (PDF Part 2 Item 4)
        # Every new message creates exactly ONE notification to the other party
        # ============================================================
        if is_admin:
            # Admin sent message → notify customer
            if complaint.customer and complaint.customer.user:
                Notification.objects.create(
                    store=Store.objects.first(),
                    user=complaint.customer.user,
                    title=f"New reply on complaint #{complaint.id}",
                    message=serializer.validated_data["message"],
                    type="system",
                    sent_via="in_app",
                    reference_type="complaint",
                    reference_id=str(complaint.id),
                )
        else:
            # Customer sent message → notify all admins
            from django.contrib.auth import get_user_model
            User = get_user_model()
            admin_users = User.objects.filter(is_staff=True)

            for admin in admin_users:
                Notification.objects.create(
                    store=Store.objects.first(),
                    user=admin,
                    title=f"New reply on complaint #{complaint.id}",
                    message=serializer.validated_data["message"],
                    type="system",
                    sent_via="in_app",
                    reference_type="complaint",
                    reference_id=str(complaint.id),
                )

        # Return the message
        response_serializer = ComplaintMessageSerializer(message)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class AdminComplaintStatusViewSet(viewsets.GenericViewSet):
    """
    Admin-only endpoints for complaints.

    PUT /api/v1/admin/complaints/{id}/status/ - Update complaint status
    """

    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    queryset = Complaint.objects.all()

    @action(detail=True, methods=["put"], url_path="status")
    def update_status(self, request, pk=None):
        """
        PUT /api/v1/admin/complaints/{id}/status/
        Admin only, explicit call only, values unchanged: open | in_progress | resolved | closed
        Sending a message must never move status automatically.
        """
        complaint = self.get_object()
        serializer = ComplaintStatusUpdateSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        new_status = serializer.validated_data["status"]
        old_status = complaint.status

        complaint.status = new_status
        complaint.save()

        # Create notification for customer
        if complaint.customer and complaint.customer.user:
            Notification.objects.create(
                store=Store.objects.first(),
                user=complaint.customer.user,
                title=f"Complaint #{complaint.id} status updated",
                message=f"Your complaint status has changed from {old_status} to {new_status}.",
                type="system",
                sent_via="in_app",
                reference_type="complaint",
                reference_id=str(complaint.id),
            )

        return Response({
            "id": complaint.id,
            "status": complaint.status,
            "message": f"Status updated to {new_status}"
        })


# ============================================================
# NOTE: PUT /api/v1/admin/complaints/{id}/respond/ is DEPRECATED
# and REMOVED as per PDF Part 2 Item 4.
# Previously it auto-set status to resolved, which was a bug.
# Now use messages/ POST + status/ PUT separately.
# ============================================================