# PATH: apps/payments/qr_image_views.py
#
# Admin-uploaded payment QR image (the QR the customer scans before
# uploading payment proof).
#
# Single-store setup for now: the store is Store.objects.first(), same as
# apps/stores/views.py::MyStoreView. When multi-store arrives, only
# _get_store() needs to change (e.g. resolve the store from the order or a
# store_id param) - the model field already lives on Store.

from django.conf import settings
from django.templatetags.static import static
from rest_framework import permissions, serializers, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.stores.models import Store
from apps.users.permissions import IsAdmin

MAX_QR_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB


def _get_store():
    return Store.objects.first()


class PaymentQRUploadSerializer(serializers.Serializer):
    # ImageField also verifies (via Pillow) that the file is a real image.
    image = serializers.ImageField(required=True)

    def validate_image(self, image):
        if image.size > MAX_QR_IMAGE_BYTES:
            raise serializers.ValidationError(
                "Image must be smaller than 5MB."
            )
        return image


def _qr_payload(request, store):
    """
    Returns the QR image URL for the store. If the admin has not uploaded
    one yet, falls back to settings.DEFAULT_PAYMENT_QR_URL (the dummy).
    """
    if store.payment_qr_image:
        url = store.payment_qr_image.url
        is_default = False
    else:
        # Dummy placeholder shipped with the app
        # (apps/payments/static/payments/default_qr.png). Optionally
        # override with settings.DEFAULT_PAYMENT_QR_URL.
        url = getattr(settings, "DEFAULT_PAYMENT_QR_URL", "") or None
        if not url:
            try:
                url = static("payments/default_qr.png")
            except ValueError:
                # Manifest storage raises if collectstatic hasn't run yet
                url = settings.STATIC_URL + "payments/default_qr.png"
        is_default = True

    if url and url.startswith("/"):
        url = request.build_absolute_uri(url)

    # Cloudinary can hand back http:// URLs; an https frontend would block
    # them as mixed content. Same http -> https fix the product/order image
    # serializers already apply (skipped for local development hosts).
    if (
        url
        and url.startswith("http://")
        and "localhost" not in url
        and "127.0.0.1" not in url
    ):
        url = "https://" + url[len("http://"):]

    return {
        "store_id": store.id,
        "qr_image_url": url,
        "is_default": is_default,
        "updated_at": store.updated_at,
    }


class StorePaymentQRView(APIView):
    """
    GET /api/v1/payments/qr/image/
    Customer side: returns the QR image the customer should scan.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        store = _get_store()
        if store is None:
            return Response(
                {"error": "Store not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(_qr_payload(request, store))


class AdminStorePaymentQRView(APIView):
    """
    Admin side (any admin of the store).

    GET    /api/v1/admin/payments/qr/image/  -> current QR
    POST   /api/v1/admin/payments/qr/image/  -> upload/replace (multipart, field: image)
    PUT    same as POST
    DELETE /api/v1/admin/payments/qr/image/  -> remove custom QR (reverts to dummy)
    """
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    parser_classes = [MultiPartParser, FormParser]

    def _store_or_404(self):
        store = _get_store()
        if store is None:
            return None, Response(
                {"error": "Store not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return store, None

    def get(self, request):
        store, err = self._store_or_404()
        if err:
            return err
        return Response(_qr_payload(request, store))

    def post(self, request):
        store, err = self._store_or_404()
        if err:
            return err

        serializer = PaymentQRUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        old_image = store.payment_qr_image if store.payment_qr_image else None

        store.payment_qr_image = serializer.validated_data["image"]
        store.save(update_fields=["payment_qr_image", "updated_at"])

        # Remove the previous file so old QRs don't pile up in storage.
        if old_image:
            try:
                old_image.delete(save=False)
            except Exception:
                pass

        return Response(_qr_payload(request, store))

    put = post

    def delete(self, request):
        store, err = self._store_or_404()
        if err:
            return err

        if store.payment_qr_image:
            try:
                store.payment_qr_image.delete(save=False)
            except Exception:
                pass
            store.payment_qr_image = None
            store.save(update_fields=["payment_qr_image", "updated_at"])

        return Response(_qr_payload(request, store))