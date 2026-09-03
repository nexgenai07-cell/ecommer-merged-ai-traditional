# PATH: apps/cart/views.py

from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from django.utils import timezone
from django.db.models import Sum

from .models import Cart, CartItem
from .serializers import (
    CartSerializer, AddToCartSerializer,
    UpdateCartItemSerializer, ApplyCouponSerializer,
)
from apps.products.models import Discount
from apps.stores.models import Store


def get_or_create_cart_for_request(request):
    """
    FIX: ab user aur session_key dono support karta hai.
    - Logged-in user: cart unke account se link hota hai (jaisa pehle tha).
    - Anonymous: request header 'X-Session-Key' se session_key liya jata hai
      (frontend/WhatsApp ye header bhejega jo chat session ka session_key hoga).
    Agar dono missing hon, error raise hota hai.

    NOTE: Do not revert this back to IsAuthenticated-only / user-based lookup.
    Anonymous/guest carts (WhatsApp flow) depend on the X-Session-Key path.
    """
    store = Store.objects.first()

    if request.user and request.user.is_authenticated:
        cart, _ = Cart.objects.get_or_create(user=request.user, store=store)
        return cart

    session_key = request.headers.get('X-Session-Key')
    if not session_key:
        return None

    cart, _ = Cart.objects.get_or_create(session_key=session_key, store=store)
    return cart


class CartView(APIView):
    """GET /api/v1/cart/"""
    # FIX: AllowAny — anonymous users bhi apna cart dekh sakein (session_key se)
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        cart = get_or_create_cart_for_request(request)
        if cart is None:
            return Response({'error': 'Login required or X-Session-Key header missing.'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(CartSerializer(cart).data)


class AddToCartView(APIView):
    """POST /api/v1/cart/add/"""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = AddToCartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = serializer.validated_data['product']
        quantity = serializer.validated_data['quantity']

        cart = get_or_create_cart_for_request(request)
        if cart is None:
            return Response({'error': 'Login required or X-Session-Key header missing.'}, status=status.HTTP_400_BAD_REQUEST)

        cart_item, created = CartItem.objects.get_or_create(
            cart=cart, product=product,
            defaults={'quantity': quantity}
        )

        if not created:
            new_quantity = cart_item.quantity + quantity
            # FIX (Cross-check, Sep 2026 — PDF Part 2 Item 5): same
            # stock -> available_stock fix as AddToCartSerializer.
            if new_quantity > product.available_stock:
                return Response(
                    {'error': f'Only {product.available_stock} units available in stock.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            cart_item.quantity = new_quantity
            cart_item.save()

        # FIX: was returning the ENTIRE cart object. Doc (API 33) says the
        # response should be { "message": "...", "cart_total_items": N } —
        # "cart_total_items" is what the frontend uses to update the cart
        # icon badge, and it did not exist anywhere in the old response.
        cart_total_items = cart.items.aggregate(total=Sum('quantity'))['total'] or 0
        return Response({
            'message': 'Product added to cart.',
            'cart_total_items': cart_total_items,
        }, status=status.HTTP_200_OK)


class UpdateCartItemView(APIView):
    """PUT /api/v1/cart/update/{item_id}/"""
    permission_classes = [permissions.AllowAny]

    def put(self, request, item_id):
        cart = get_or_create_cart_for_request(request)
        if cart is None:
            return Response({'error': 'Login required or X-Session-Key header missing.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            cart_item = cart.items.get(id=item_id)
        except CartItem.DoesNotExist:
            return Response({'error': 'Cart item not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = UpdateCartItemSerializer(data=request.data, context={'cart_item': cart_item})
        serializer.is_valid(raise_exception=True)
        quantity = serializer.validated_data['quantity']

        # FIX: was returning the ENTIRE cart object. Doc (API 34) says the
        # response should be { "message": "...", "item_total": "..." } —
        # "item_total" did not exist anywhere in the old response.
        if quantity == 0:
            cart_item.delete()
            return Response({'message': 'Cart updated.', 'item_total': '0.00'}, status=status.HTTP_200_OK)

        cart_item.quantity = quantity
        cart_item.save()
        item_total = cart_item.product.price * cart_item.quantity

        return Response({
            'message': 'Cart updated.',
            'item_total': str(item_total),
        }, status=status.HTTP_200_OK)


class RemoveCartItemView(APIView):
    """DELETE /api/v1/cart/remove/{item_id}/"""
    permission_classes = [permissions.AllowAny]

    def delete(self, request, item_id):
        cart = get_or_create_cart_for_request(request)
        if cart is None:
            return Response({'error': 'Login required or X-Session-Key header missing.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            cart_item = cart.items.get(id=item_id)
        except CartItem.DoesNotExist:
            return Response({'error': 'Cart item not found.'}, status=status.HTTP_404_NOT_FOUND)

        cart_item.delete()
        # FIX: was returning the ENTIRE cart object; doc (API 35) only
        # documents { "message": "Item removed from cart." }.
        return Response({'message': 'Item removed from cart.'}, status=status.HTTP_200_OK)


class ClearCartView(APIView):
    """DELETE /api/v1/cart/clear/"""
    permission_classes = [permissions.AllowAny]

    def delete(self, request):
        cart = get_or_create_cart_for_request(request)
        if cart is None:
            return Response({'error': 'Login required or X-Session-Key header missing.'}, status=status.HTTP_400_BAD_REQUEST)

        cart.items.all().delete()
        cart.coupon = None
        cart.save()
        return Response({'message': 'Cart cleared.'}, status=status.HTTP_200_OK)


class ApplyCouponView(APIView):
    """POST /api/v1/cart/apply-coupon/"""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = ApplyCouponSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data['code']

        try:
            discount = Discount.objects.get(code=code, is_active=True)
        except Discount.DoesNotExist:
            return Response({'error': 'Invalid or inactive coupon code.'}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        if not (discount.start_date <= now <= discount.end_date):
            return Response({'error': 'This coupon has expired or is not active yet.'}, status=status.HTTP_400_BAD_REQUEST)

        cart = get_or_create_cart_for_request(request)
        if cart is None:
            return Response({'error': 'Login required or X-Session-Key header missing.'}, status=status.HTTP_400_BAD_REQUEST)

        subtotal = sum(item.product.price * item.quantity for item in cart.items.all())

        if discount.min_order_amount and subtotal < discount.min_order_amount:
            return Response(
                {'error': f'Minimum order amount of Rs. {discount.min_order_amount} required for this coupon.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        cart.coupon = discount
        cart.save()

        # FIX: was returning the ENTIRE cart object. Doc (API 37) says the
        # response should be { "message": "...", "discount_amount": "...", "total": "..." }.
        cart_serializer = CartSerializer(cart)
        return Response({
            'message': 'Coupon applied.',
            'discount_amount': str(cart_serializer.get_discount_amount(cart)),
            'total': str(cart_serializer.get_total(cart)),
        }, status=status.HTTP_200_OK)


class RemoveCouponView(APIView):
    """DELETE /api/v1/cart/remove-coupon/"""
    permission_classes = [permissions.AllowAny]

    def delete(self, request):
        cart = get_or_create_cart_for_request(request)
        if cart is None:
            return Response({'error': 'Login required or X-Session-Key header missing.'}, status=status.HTTP_400_BAD_REQUEST)

        cart.coupon = None
        cart.save()
        # FIX: was returning the ENTIRE cart object; doc (API 38) only
        # documents { "message": "Coupon removed." }.
        return Response({'message': 'Coupon removed.'}, status=status.HTTP_200_OK)