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
        serializer.is_valid(raise_exception=True)
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
        serializer.is_valid(raise_exception=True)
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