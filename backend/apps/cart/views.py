# PATH: apps/cart/views.py
import uuid
from rest_framework import status, permissions
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from django.utils import timezone
from django.db.models import Sum
from sympy import python

from .models import Cart, CartItem
from .serializers import (
    CartSerializer, AddToCartSerializer,
    UpdateCartItemSerializer, ApplyCouponSerializer,
)
from apps.products.models import Discount
from apps.stores.models import Store


def get_or_create_cart_for_request(request):
    """
    Returns:
        (cart, session_key, is_new_session)

    Authenticated:
        Uses the user's account cart.

    Anonymous:
        Uses X-Cart-Session when provided.
        If no session key is provided, generates a new one.
    """
    store = Store.objects.first()

    # Authenticated user -> account cart
    if request.user and request.user.is_authenticated:
        cart, _ = Cart.objects.get_or_create(
            user=request.user,
            store=store,
        )
        return cart, None, False

    # Guest -> accept the required cart header.
    # Keep X-Session-Key as backward compatibility for existing flows.
    session_key = (
        request.headers.get("X-Cart-Session")
        or request.headers.get("X-Session-Key")
    )

    is_new_session = False

    if not session_key:
        session_key = uuid.uuid4().hex
        is_new_session = True

    cart, created = Cart.objects.get_or_create(
        session_key=session_key,
        store=store,
    )

    # If a key was supplied but the cart didn't exist, this is still
    # the first cart request for that guest session.
    if created:
        is_new_session = True

    return cart, session_key, is_new_session

def merge_guest_cart_into_user_cart(request, user):
    """
    Merge the guest cart identified by X-Cart-Session into
    the authenticated user's account cart.

    If the same product exists in both carts, quantities are added
    together and capped at the product's available stock.

    Returns True if a guest cart was found and merged.
    """

    session_key = (
        request.headers.get("X-Cart-Session")
        or request.headers.get("X-Session-Key")
    )

    if not session_key:
        return False

    store = Store.objects.first()

    if not store:
        return False

    try:
        guest_cart = Cart.objects.get(
            session_key=session_key,
            user__isnull=True,
            store=store,
        )
    except Cart.DoesNotExist:
        return False

    with transaction.atomic():

        account_cart, _ = Cart.objects.get_or_create(
            user=user,
            store=store,
        )

        for guest_item in guest_cart.items.select_related("product"):
            product = guest_item.product

            available_stock = max(
                product.available_stock,
                0,
            )

            if available_stock <= 0:
                continue

            account_item = CartItem.objects.filter(
                cart=account_cart,
                product=product,
            ).first()

            if account_item:
                merged_quantity = min(
                    account_item.quantity + guest_item.quantity,
                    available_stock,
                )

                account_item.quantity = merged_quantity
                account_item.save(update_fields=["quantity"])

            else:
                account_quantity = min(
                    guest_item.quantity,
                    available_stock,
                )

                CartItem.objects.create(
                    cart=account_cart,
                    product=product,
                    quantity=account_quantity,
                )

        # Guest cart has successfully been processed.
        # Remove it so the same guest cart cannot be merged again.
        guest_cart.delete()

    return True

class CartView(APIView):
    """GET /api/v1/cart/"""
    # FIX: AllowAny — anonymous users bhi apna cart dekh sakein (session_key se)
    permission_classes = [permissions.AllowAny]

    def get(self, request):
       cart, session_key, is_new_session = get_or_create_cart_for_request(request)

       data = CartSerializer(cart).data

    # Guest cart session key must be returned so frontend
    # can store it in localStorage and send it on future requests.
       if session_key:
         data["session_key"] = session_key

       return Response(data)

class AddToCartView(APIView):
    """POST /api/v1/cart/add/"""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = AddToCartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product = serializer.validated_data['product']
        quantity = serializer.validated_data['quantity']

        cart, session_key, is_new_session = get_or_create_cart_for_request(request)

        cart_item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={'quantity': quantity}
        )

        if not created:
            new_quantity = cart_item.quantity + quantity
            cart_item.quantity = min(new_quantity, product.available_stock)
            cart_item.save()

        # Calculate total quantity of all items in the cart.
        cart_total_items = (
            cart.items.aggregate(total=Sum('quantity'))['total'] or 0
        )

        response_data = {
            'message': 'Product added to cart.',
            'cart_total_items': cart_total_items,
        }

        # Return the session key for guest carts.
        if session_key:
          response_data['session_key'] = session_key

        return Response(
            response_data,
            status=status.HTTP_200_OK
        )

class UpdateCartItemView(APIView):
    """PUT /api/v1/cart/update/{item_id}/"""
    permission_classes = [permissions.AllowAny]

    def put(self, request, item_id):
        cart, session_key, is_new_session = get_or_create_cart_for_request(request)

        if cart is None:
            return Response(
                {'error': 'Login required or X-Session-Key header missing.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            cart_item = cart.items.get(id=item_id)
        except CartItem.DoesNotExist:
            response_data = {
                'error': 'Cart item not found.'
            }

            if session_key:
                response_data['session_key'] = session_key

            return Response(
                response_data,
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = UpdateCartItemSerializer(
            data=request.data,
            context={'cart_item': cart_item}
        )
        serializer.is_valid(raise_exception=True)

        quantity = serializer.validated_data['quantity']

        if quantity == 0:
            cart_item.delete()

            response_data = {
                'message': 'Cart updated.',
                'item_total': '0.00',
            }

            if session_key:
                response_data['session_key'] = session_key

            return Response(
                response_data,
                status=status.HTTP_200_OK
            )

        cart_item.quantity = quantity
        cart_item.save()

        item_total = cart_item.product.price * cart_item.quantity

        response_data = {
            'message': 'Cart updated.',
            'item_total': str(item_total),
        }

        if session_key:
            response_data['session_key'] = session_key

        return Response(
            response_data,
            status=status.HTTP_200_OK
        )


class RemoveCartItemView(APIView):
    """DELETE /api/v1/cart/remove/{item_id}/"""
    permission_classes = [permissions.AllowAny]

    def delete(self, request, item_id):
        cart, session_key, is_new_session = get_or_create_cart_for_request(request)

        if cart is None:
            return Response(
                {'error': 'Login required or X-Session-Key header missing.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            cart_item = cart.items.get(id=item_id)
        except CartItem.DoesNotExist:
            response_data = {
                'error': 'Cart item not found.'
            }

            if session_key:
                response_data['session_key'] = session_key

            return Response(
                response_data,
                status=status.HTTP_404_NOT_FOUND
            )

        cart_item.delete()

        response_data = {
            'message': 'Item removed from cart.'
        }

        if session_key:
            response_data['session_key'] = session_key

        return Response(
            response_data,
            status=status.HTTP_200_OK
        )


class ClearCartView(APIView):
    """DELETE /api/v1/cart/clear/"""
    permission_classes = [permissions.AllowAny]

    def delete(self, request):
        cart, session_key, is_new_session = get_or_create_cart_for_request(request)

        if cart is None:
            return Response(
                {'error': 'Login required or X-Session-Key header missing.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        cart.items.all().delete()
        cart.coupon = None
        cart.save()

        response_data = {
            'message': 'Cart cleared.'
        }

        if session_key:
            response_data['session_key'] = session_key

        return Response(
            response_data,
            status=status.HTTP_200_OK
        )


class ApplyCouponView(APIView):
    """POST /api/v1/cart/apply-coupon/"""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        # Bootstrap/reuse the cart session FIRST.
        cart, session_key, is_new_session = get_or_create_cart_for_request(request)

        if cart is None:
            return Response(
                {'error': 'Login required or X-Session-Key header missing.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = ApplyCouponSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        code = serializer.validated_data['code']

        try:
            discount = Discount.objects.get(
                code=code,
                is_active=True
            )
        except Discount.DoesNotExist:
            response_data = {
                'error': 'Invalid or inactive coupon code.'
            }

            if session_key:
                response_data['session_key'] = session_key

            return Response(
                response_data,
                status=status.HTTP_400_BAD_REQUEST
            )

        now = timezone.now()

        if not (discount.start_date <= now <= discount.end_date):
            response_data = {
                'error': 'This coupon has expired or is not active yet.'
            }

            if session_key:
                response_data['session_key'] = session_key

            return Response(
                response_data,
                status=status.HTTP_400_BAD_REQUEST
            )

        subtotal = sum(
            item.product.price * item.quantity
            for item in cart.items.all()
        )

        if discount.min_order_amount and subtotal < discount.min_order_amount:
            response_data = {
                'error': (
                    f'Minimum order amount of Rs. '
                    f'{discount.min_order_amount} required for this coupon.'
                )
            }

            if session_key:
                response_data['session_key'] = session_key

            return Response(
                response_data,
                status=status.HTTP_400_BAD_REQUEST
            )

        cart.coupon = discount
        cart.save()

        cart_serializer = CartSerializer(cart)

        response_data = {
            'message': 'Coupon applied.',
            'discount_amount': str(
                cart_serializer.get_discount_amount(cart)
            ),
            'total': str(
                cart_serializer.get_total(cart)
            ),
        }

        if session_key:
            response_data['session_key'] = session_key

        return Response(
            response_data,
            status=status.HTTP_200_OK
        )


class RemoveCouponView(APIView):
    """DELETE /api/v1/cart/remove-coupon/"""
    permission_classes = [permissions.AllowAny]

    def delete(self, request):
        cart, session_key, is_new_session = get_or_create_cart_for_request(request)

        if cart is None:
            return Response(
                {'error': 'Login required or X-Session-Key header missing.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        cart.coupon = None
        cart.save()

        response_data = {
            'message': 'Coupon removed.'
        }

        if session_key:
            response_data['session_key'] = session_key

        return Response(
            response_data,
            status=status.HTTP_200_OK
        )
