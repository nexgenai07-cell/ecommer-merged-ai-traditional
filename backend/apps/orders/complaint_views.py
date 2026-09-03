# PATH: apps/orders/complaint_views.py

from rest_framework import status, permissions, generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.db.models import Q
import re

from apps.returns.models import Complaint, ComplaintMessage
from apps.returns.complaint_serializers import (
    ComplaintSerializer,
    CreateComplaintSerializer,
    AdminComplaintStatusSerializer,
    ComplaintMessageSerializer,
    CreateComplaintMessageSerializer,
)
from .models import Order, Customer
from apps.users.permissions import IsAdmin
from apps.users.models import User
from apps.notifications.utils import create_notification
from core.pagination import StandardResultsPagination

# Handles both:
# GET  -> List complaints
# POST -> Create a new complaint
class CreateComplaintView(generics.ListCreateAPIView):
    """
    POST /api/v1/complaints/ -> submit a complaint
    GET  /api/v1/complaints/ -> customer sees own complaints, admin sees all

    FIX (Postman testing — 09 Jul 2026): doc (API 65) expects
    {count, next, previous, results} for the GET/list action.
    pagination_class wasn't attached here before, so the response was
    missing the next/previous keys. Now explicitly attached.

    FIX (A3): 'status' and 'search' query params now work (page already
    did, via pagination_class above). 'search' matches the complaint's
    reference number — the model has no dedicated reference-number
    field, so formats like "CMP-36", "CP-36", "#CP-36", or a bare "36"
    are all matched against the primary key — and/or the complaint's
    message text.

    NEW (Follow-up v8, item 2.1): 'priority' (normal / urgent) now also
    filters, combined with 'status' and 'search'.
    """

    serializer_class = ComplaintSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    pagination_class = StandardResultsPagination

# Admin can view every complaint in the system.
    def get_queryset(self):
        user = self.request.user
        if user.role == "admin":
            qs = Complaint.objects.all().order_by("-created_at")
        else:
# Customers can only see their own complaints.
            qs = Complaint.objects.filter(
                customer__user=user
            ).order_by("-created_at")

        params = self.request.query_params

        status_param = params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)

        # NEW (Follow-up v8, item 2.1): 'priority' was being sent by the
        # frontend already but was never read here — silent no-op. Same
        # convention as 'status' above (exact match, combines with it and
        # with 'search').
        priority_param = params.get("priority")
        if priority_param:
            qs = qs.filter(priority=priority_param)

        # FIX (A3): reference number format ka jhamela avoid karne k liye
        # regex se match kiya h — "CMP-36", "CP-36", "#CP-36", "#CMP36",
        # ya sirf "36" — sab se id=36 resolve ho jata h. Poori string
        # anchor (^...$) k sath match hoti h, is liye normal message-text
        # searches (jinme numbers k sath asal alfaz bhi hon) galti se
        # id-match nahi ban jate.
        search = params.get("search")
        if search:
            search = search.strip()
            search_filter = Q(message__icontains=search)
            ref_match = re.match(r'^#?\s*[A-Za-z]{0,6}-?\s*(\d+)$', search)
            if ref_match:
                search_filter |= Q(id=int(ref_match.group(1)))
            qs = qs.filter(search_filter)

        return qs

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

        # Ensure the supplied order belongs to the logged-in customer
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

# Returns details of one complaint.
class ComplaintDetailView(generics.RetrieveAPIView):
    """GET /api/v1/complaints/{id}/"""

    serializer_class = ComplaintSerializer
    permission_classes = [permissions.IsAuthenticated]

# Admin can open any complaint.
    def get_queryset(self):
        user = self.request.user
        if user.role == "admin":
            return Complaint.objects.all()

        return Complaint.objects.filter(customer__user=user) # Customer can only open their own complaints.

# Retrieves the requested complaint.
    def retrieve(self, request, *args, **kwargs):
        complaint = self.get_object()
        serializer = ComplaintSerializer(
            complaint,
            context={"request": request},
        )
        return Response(serializer.data)

# Allows admin to change complaint status.
class AdminComplaintStatusUpdateView(APIView):
    """
    PUT /api/v1/admin/complaints/{id}/status/

    FIX (Postman testing — 09 Jul 2026): doc (API 67) expects only
    {"message": "Complaint status updated."} — the full
    ComplaintSerializer(complaint).data object was being returned
    before, which doesn't match.
    """

    permission_classes = [permissions.IsAuthenticated, IsAdmin]

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

# NEW (Backend Change Request v2, Part 2 — Item 4 / Issue 7):
# AdminComplaintRespondView (PUT .../respond/) is REMOVED per spec — it
# used to auto-set status to "resolved" on every reply, which is exactly
# the bug being fixed here. Replaced by the two views below.
#
# NOTE for whoever maintains the "response"/"resolved_by" fields still on
# the Complaint model: nothing writes to them anymore (respond/ was the
# only writer). Left on the model rather than migrated out, since dropping
# columns wasn't asked for here and old complaints may still have historic
# data in them worth keeping readable.
#
# RESTORED (Sep 2026 cross-check): this whole Item 4 implementation had
# been reverted back to the old respond/ endpoint in a later round of
# work (looks like it was built on an older base) — putting it back here.


def _notify_other_party(complaint, sender):
    """
    FLOW: called from ComplaintMessageListCreateView.create() right after
    a message is saved. Sends exactly ONE notification to "the other
    party", per spec.

    DESIGN NOTE (flagging this — spec doesn't define multi-admin
    behaviour): this project has no per-complaint "assigned admin" concept
    (Complaint.resolved_by is only ever set by the old respond/ flow,
    which no longer runs). So "the admin party" resolves to: whichever
    admin already resolved_by-owns this complaint if set, else just the
    first admin account in the system. That keeps this at exactly one
    notification row, matching the spec's wording literally, instead of
    fanning out to every admin (which the spec doesn't ask for and would
    read as more than "exactly ONE"). If there end up being multiple
    admins who all need to see new customer messages, this needs a real
    "assigned admin" field — let me know and I'll add it.
    """
    if sender == "customer":
        target_user = complaint.resolved_by or User.objects.filter(role="admin").first()
        if not target_user:
            return
    else:
        target_user = complaint.customer.user

    create_notification(
        user=target_user,
        title=f"New reply on complaint #{complaint.id}",
        message=(
            f"New reply on complaint #{complaint.id}."
            if sender == "admin"
            else f"New customer reply on complaint #{complaint.id}."
        ),
        notification_type="system",
        reference_type="complaint",
        reference_id=complaint.id,
    )


# GET  /api/v1/complaints/{id}/messages/  -> full thread, chronological, both roles
# POST /api/v1/complaints/{id}/messages/  -> {"message": "string"}
# Allowed for the complaint's owning customer OR any admin. Posting a
# message NEVER changes status — status stays exclusively on
# AdminComplaintStatusUpdateView (above), explicit-only, per spec.
class ComplaintMessageListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None  # spec response shape is a flat {"results": [...]}, no count/next/previous

    def _get_complaint(self):
        user = self.request.user
        try:
            if user.role == "admin":
                return Complaint.objects.get(id=self.kwargs["pk"])
            return Complaint.objects.get(id=self.kwargs["pk"], customer__user=user)
        except Complaint.DoesNotExist:
            return None

    def get_queryset(self):
        complaint = self._get_complaint()
        if complaint is None:
            return ComplaintMessage.objects.none()
        return ComplaintMessage.objects.filter(complaint=complaint).order_by("created_at")

    def list(self, request, *args, **kwargs):
        complaint = self._get_complaint()
        if complaint is None:
            return Response({"error": "Complaint not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = ComplaintMessageSerializer(self.get_queryset(), many=True)
        return Response({"results": serializer.data})

    def create(self, request, *args, **kwargs):
        complaint = self._get_complaint()
        if complaint is None:
            return Response({"error": "Complaint not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = CreateComplaintMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # NOTE: this project's ComplaintMessage.sender is a real stored
        # CharField (not a derived property), so it has to be set
        # explicitly here from the requesting user's role.
        sender = "admin" if request.user.role == "admin" else "customer"

        complaint_message = ComplaintMessage.objects.create(
            complaint=complaint,
            sender=sender,
            sender_user=request.user,
            message=serializer.validated_data["message"],
        )

        # Exactly one notification to the other party — never touches status.
        _notify_other_party(complaint, complaint_message.sender)

        return Response(
            ComplaintMessageSerializer(complaint_message).data,
            status=status.HTTP_201_CREATED,
        )