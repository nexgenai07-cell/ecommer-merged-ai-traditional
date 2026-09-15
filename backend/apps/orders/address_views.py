# PATH: apps/orders/address_views.py

from rest_framework import status, permissions, generics
from rest_framework.views import APIView
from rest_framework.response import Response

from .models import Address
from .address_serializers import AddressSerializer, AddressWriteSerializer
from .views import get_or_create_customer
from apps.stores.models import Store
from apps.users.permissions import IsCustomer


def _get_customer(request):
    """Same store-resolution pattern used elsewhere in this app
    (single-store setup -> Store.objects.first())."""
    store = Store.objects.first()
    return get_or_create_customer(request.user, store_id=store.id if store else 1)


# FIX (Sep 2026 — "Failed to save this address" showing with no real
# reason): DRF's default serializer.errors is a dict keyed by field name,
# e.g. {"shipping_address": ["Shipping address looks too short..."]}.
# Every OTHER error response in this API (CheckoutView, OTP send/verify,
# AddressSetDefaultView below, etc.) instead returns a flat
# {"error": "<message>"} — the frontend's shared error handler only ever
# reads that "error" key, so an address validation failure (e.g. a
# street address under 8 characters, an invalid postal code, a bad phone
# format) was silently falling through to a generic fallback message
# with no indication of what was actually wrong. This picks the first
# validation message (checked in a stable field order, so the same bad
# input always reports the same thing) and reshapes it into that same
# {"error": "..."} convention the rest of the API already uses.
def _first_error_message(errors):
    field_order = [
        "label",
        "shipping_address",
        "city",
        "postal_code",
        "phone",
        "non_field_errors",
    ]
    for field in field_order:
        messages = errors.get(field)
        if messages:
            return str(messages[0])

    # Fallback for any field outside the expected list.
    for messages in errors.values():
        if messages:
            return str(messages[0]) if isinstance(messages, list) else str(messages)

    return "Please check the address details and try again."


# GET /api/v1/addresses/  — list this customer's saved addresses
# POST /api/v1/addresses/ — create a new address (never overwrites an
# existing one — this is always an INSERT, never an UPDATE).
class AddressListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated, IsCustomer]
    # NOTE: locked spec response shape for GET is exactly
    # { "results": [...] } — no count/next/previous. Without this, the
    # global DEFAULT_PAGINATION_CLASS (StandardResultsPagination) would
    # apply and add count/next/previous, which the spec doesn't ask for.
    pagination_class = None

    def get_queryset(self):
        return Address.objects.filter(customer=_get_customer(self.request))

    def get_serializer_class(self):
        if self.request.method == "POST":
            return AddressWriteSerializer
        return AddressSerializer

    def list(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response({"results": serializer.data})

    def perform_create(self, serializer):
        serializer.save(customer=_get_customer(self.request))

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": _first_error_message(serializer.errors)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        self.perform_create(serializer)
        # Respond with the full address shape (including "id"), same as
        # the list response, not just the write-serializer's fields.
        return Response(
            AddressSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED,
        )


# PUT /api/v1/addresses/{id}/    — edit that one address only
# DELETE /api/v1/addresses/{id}/
class AddressDetailView(generics.UpdateAPIView, generics.DestroyAPIView):
    permission_classes = [permissions.IsAuthenticated, IsCustomer]
    serializer_class = AddressWriteSerializer

    def get_queryset(self):
        return Address.objects.filter(customer=_get_customer(self.request))

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        if not serializer.is_valid():
            return Response(
                {"error": _first_error_message(serializer.errors)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        self.perform_update(serializer)
        return Response(AddressSerializer(serializer.instance).data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# PUT /api/v1/addresses/{id}/set-default/ — no body. Sets this one to
# is_default: true; Address.save() takes care of unsetting whichever
# address was previously default for this customer.
class AddressSetDefaultView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsCustomer]

    def put(self, request, pk):
        try:
            address = Address.objects.get(pk=pk, customer=_get_customer(request))
        except Address.DoesNotExist:
            return Response(
                {"error": "Address not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        address.is_default = True
        address.save()

        return Response(AddressSerializer(address).data)