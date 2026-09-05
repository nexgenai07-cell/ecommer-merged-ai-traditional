from django.contrib.auth import get_user_model

from rest_framework import status, permissions, generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from apps.returns.models import Complaint, ComplaintMessage
from apps.returns.complaint_serializers import (
    ComplaintSerializer,
    CreateComplaintSerializer,
    AdminComplaintStatusSerializer,
    AdminComplaintRespondSerializer,
)
from apps.notifications.utils import create_notification
from .models import Order, Customer
from apps.users.permissions import IsAdmin
from core.pagination import StandardResultsPagination


class CreateComplaintView(generics.ListCreateAPIView):
    """
    POST /api/v1/complaints/ -> creates a complaint
    GET  /api/v1/complaints/ -> lists complaints visible to the user
    """

    serializer_class = ComplaintSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    pagination_class = StandardResultsPagination

    # Admins can see every complaint; customers can see only their own.
    def get_queryset(self):
        if self.request.user.role == "admin":
            return Complaint.objects.all().order_by("-created_at")

        return Complaint.objects.filter(
            customer__user=self.request.user
        ).order_by("-created_at")

    # Creates a complaint for the current customer's own order.
    def create(self, request, *args, **kwargs):
        serializer = CreateComplaintSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        customer = Customer.objects.filter(user=request.user).first()
        if not customer:
            return Response(
                {"error": "No customer profile found. Place an order first."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order = serializer.validated_data.get("order")

        if order and order.customer.user != request.user:
            return Response(
                {"error": "Invalid order."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        complaint = serializer.save(
            customer=customer,
            order=order,
            status="open",
        )

        return Response(
            ComplaintSerializer(
                complaint,
                context={"request": request},
            ).data,
            status=status.HTTP_201_CREATED,
        )


class ComplaintDetailView(generics.RetrieveAPIView):
    """GET /api/v1/complaints/{id}/"""

    serializer_class = ComplaintSerializer
    permission_classes = [permissions.IsAuthenticated]

    # Admins can open all complaints; customers can open only their own.
    def get_queryset(self):
        if self.request.user.role == "admin":
            return Complaint.objects.all()

        return Complaint.objects.filter(customer__user=self.request.user)

    # Returns one complaint.
    def retrieve(self, request, *args, **kwargs):
        complaint = self.get_object()
        serializer = ComplaintSerializer(
            complaint,
            context={"request": request},
        )
        return Response(serializer.data)


class ComplaintMessageView(APIView):
    """POST /api/v1/complaints/{id}/messages/"""

    permission_classes = [permissions.IsAuthenticated]

    # Adds one private complaint-thread message and creates exactly one
    # notification for the party that did not send that message.
    def post(self, request, pk):
        try:
            complaint = Complaint.objects.select_related(
                "customer__user",
                "customer__store",
                "order__store",
                "resolved_by",
            ).get(id=pk)
        except Complaint.DoesNotExist:
            return Response(
                {"error": "Complaint not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        sender_is_admin = request.user.role == "admin"

        if (
            not sender_is_admin
            and complaint.customer.user_id != request.user.id
        ):
            return Response(
                {"error": "Complaint not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        message = request.data.get("message")

        if not isinstance(message, str) or not message.strip():
            return Response(
                {"error": "message is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # An admin message goes to the customer. A customer message goes
        # to the assigned admin, or the first active admin as fallback.
        if sender_is_admin:
            recipient = complaint.customer.user
        else:
            recipient = (
                complaint.resolved_by
                or get_user_model().objects.filter(
                    role="admin",
                    is_active=True,
                ).first()
            )

        if recipient is None:
            return Response(
                {"error": "No complaint administrator is available."},
                status=status.HTTP_409_CONFLICT,
            )

        complaint_message = ComplaintMessage.objects.create(
            complaint=complaint,
            sender=request.user,
            message=message.strip(),
        )

        create_notification(
            user=recipient,
            store=(
                complaint.order.store
                if complaint.order
                else complaint.customer.store
            ),
            title=f"New reply on complaint #{complaint.id}",
            message=f"There is a new reply on your complaint #{complaint.id}.",
            notification_type="system",
            reference_type="complaint",
            reference_id=complaint.id,
        )

        return Response(
            {
                "id": complaint_message.id,
                "complaint": complaint.id,
                "message": complaint_message.message,
                "created_at": complaint_message.created_at,
            },
            status=status.HTTP_201_CREATED,
        )


class AdminComplaintStatusUpdateView(APIView):
    """PUT /api/v1/admin/complaints/{id}/status/"""

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    # Changes the status of a complaint.
    def put(self, request, pk):
        try:
            complaint = Complaint.objects.get(id=pk)
        except Complaint.DoesNotExist:
            return Response(
                {"error": "Complaint not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = AdminComplaintStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        complaint.status = serializer.validated_data["status"]
        complaint.save()

        return Response({"message": "Complaint status updated."})


class AdminComplaintRespondView(APIView):
    """PUT /api/v1/admin/complaints/{id}/respond/"""

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    # Preserves the legacy admin response endpoint.
    def put(self, request, pk):
        try:
            complaint = Complaint.objects.get(id=pk)
        except Complaint.DoesNotExist:
            return Response(
                {"error": "Complaint not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = AdminComplaintRespondSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        complaint.response = serializer.validated_data["response"]
        complaint.resolved_by = request.user
        complaint.status = "resolved"
        complaint.save()

        return Response(
            {
                "message": "Response sent.",
                "status": complaint.status,
            }
        )