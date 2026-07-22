# PATH: apps/ai/tools/cart_order_tools.py

# FLOW: shopping_agent.py se yahan aata hai (get_cart_order_tools call
# hoti hai). Ye tools DIRECT Django ORM use karte hain (Qdrant nahi,
# koi HTTP call nahi) — kyunke ye AI agent usi Django process ke andar
# chal raha hai jahan models available hain.

from decimal import Decimal
from django.db import transaction
from langchain_core.tools import tool

from apps.cart.models import Cart, CartItem     # FLOW → cart database tables
from apps.products.models import Product
from apps.stores.models import Store
from apps.orders.models import Customer, Order, OrderItem, Payment      # FLOW → order database tables
from apps.orders.views import generate_order_number
from typing import Optional

def _get_or_create_cart(user, session_key):
    store = Store.objects.first()
    if user is not None and user.is_authenticated:
        cart, _ = Cart.objects.get_or_create(user=user, store=store)
    else:
        cart, _ = Cart.objects.get_or_create(session_key=session_key, store=store)
    return cart


def get_cart_order_tools(session_key: str, user=None):
    """Is chat session ke liye bound tools return karta hai."""

    @tool
    def add_to_cart(product_id: int, quantity: int = 1) -> dict:
        """Add a product to the customer's cart. Use this when the customer
        clearly wants to buy/add a specific product. product_id must come
        from a previous search_products or get_product_details result."""
        if quantity is None:
            quantity = 1

            # FLOW: yahan seedha Product/Cart/CartItem models pe query chalti hai — DB tak jaata hai
            
        try:
            product = Product.objects.select_related('category').get(id=product_id, is_active=True)
        except Product.DoesNotExist:
            return {'success': False, 'error': 'Product not found.'}

        if product.stock < quantity:
            return {'success': False, 'error': f'Only {product.stock} units available in stock.'}

        cart = _get_or_create_cart(user, session_key)

        cart_item, created = CartItem.objects.get_or_create(
            cart=cart, product=product, defaults={'quantity': quantity}
        )
        if not created:
            new_quantity = cart_item.quantity + quantity
            if new_quantity > product.stock:
                return {'success': False, 'error': f'Only {product.stock} units available in stock.'}
            cart_item.quantity = new_quantity
            cart_item.save()

        cart_total_items = sum(i.quantity for i in cart.items.all())
        return {
            'success': True,
            'message': f'{product.name} added to cart.',
            'product_id': product.id,                                          # NEW
            'category_id': product.category.id if product.category else None,  # NEW
            'product_name': product.name,
            'price': float(product.price),
            'quantity': cart_item.quantity,
            'cart_total_items': cart_total_items,
        }

    @tool
    def create_order(shipping_address: str, notes: str = "", guest_name: Optional[str] = None, guest_phone: Optional[str] = None) -> dict:
        """Create an order (checkout) using everything currently in the
        customer's cart.

        GUEST CHECKOUT IS ALLOWED: if the customer is NOT logged in, you can
        still place the order — but you MUST first collect their full name
        (guest_name) and phone number (guest_phone) in the conversation, in
        addition to the shipping_address. If any of these are missing for a
        guest, do not call this tool yet — ask the customer for the missing
        info first."""
        if notes is None:
            notes = ""

        cart = _get_or_create_cart(user, session_key)
        if not cart.items.exists():
            return {'success': False, 'error': 'Cart is empty. Add some products first.'}

        is_logged_in = user is not None and user.is_authenticated

        if not is_logged_in and (not guest_name or not guest_phone):
            return {
                'success': False,
                'error': (
                    'Guest checkout requires the customer\'s full name and phone number. '
                    'Ask them for their name and phone number, then call create_order again '
                    'with guest_name and guest_phone filled in.'
                ),
            }
        # ... baaki function body same rahega

        with transaction.atomic():
            cart_items = list(cart.items.select_related('product').select_for_update().all())

            out_of_stock = [i.product.name for i in cart_items if i.product.stock < i.quantity]
            if out_of_stock:
                return {
                    'success': False,
                    'error': f"These items are no longer available in the requested quantity: {', '.join(out_of_stock)}",
                }

            subtotal = sum(i.product.price * i.quantity for i in cart_items)
            discount_amount = Decimal('0')
            if cart.coupon:
                if cart.coupon.type == 'percent':
                    discount_amount = (subtotal * cart.coupon.value) / 100
                else:
                    discount_amount = cart.coupon.value
                discount_amount = min(discount_amount, subtotal)

            total_amount = subtotal - discount_amount

            if is_logged_in:
                customer, _ = Customer.objects.get_or_create(
                    user=user, store_id=cart.store_id,
                    defaults={'name': user.name, 'phone': user.phone or '', 'email': user.email},
                )
            else:
                customer, _ = Customer.objects.get_or_create(
                    phone=guest_phone, store_id=cart.store_id, user=None,
                    defaults={'name': guest_name},
                )

            order = Order.objects.create(
                store_id=cart.store_id,
                customer=customer,
                order_number=generate_order_number(),
                total_amount=total_amount,
                discount_amount=discount_amount,
                status='pending_payment',
                shipping_address=shipping_address,
                notes=notes,
            )

            order_items_summary = []
            for item in cart_items:
                OrderItem.objects.create(
                    order=order,
                    product=item.product,
                    product_name=item.product.name,
                    price=item.product.price,
                    quantity=item.quantity,
                    total_price=item.product.price * item.quantity,
                )
                item.product.stock -= item.quantity
                item.product.save()

                order_items_summary.append({
                    'product_id': item.product.id,
                    'category_id': item.product.category.id if item.product.category else None,
                    'product_name': item.product.name,
                    'quantity': item.quantity,
                })

            Payment.objects.create(order=order, status='pending', amount=total_amount)

            cart.items.all().delete()
            cart.coupon = None
            cart.save()

        result = {
            'success': True,
            'order_number': order.order_number,
            'total_amount': float(order.total_amount),
            'status': order.status,
            'items': order_items_summary,  # NEW — product_id/category_id per item
        }

        if not is_logged_in:
            result['note'] = (
                'This order was placed as a guest. To track or cancel it later, '
                'the customer must create an account / log in.'
            )

        return result

    @tool
    def track_order(order_number: str) -> dict:
        """Get the current status and tracking info of an existing order.
        order_number is REQUIRED — if the customer hasn't given their order
        number yet, ask them for it before calling this tool. The customer
        must also be logged in, and the order must belong to them."""
        if not order_number:
            return {'success': False, 'error': 'order_number is required. Ask the customer for their order number.'}

        if user is None or not user.is_authenticated:
            return {'success': False, 'error': 'Customer is not logged in. Ask them to log in first before tracking an order.'}

        try:
            order = Order.objects.get(order_number=order_number, customer__user=user)
        except Order.DoesNotExist:
            return {'success': False, 'error': 'Order not found.'}

        return {
            'success': True,
            'order_number': order.order_number,
            'status': order.status,
            'tracking_number': order.tracking_number,
            'updated_at': str(order.updated_at),
        }

    @tool
    def cancel_order(order_number: str) -> dict:
        """Cancel an existing order if it's still eligible (not already
        delivered or cancelled). order_number is REQUIRED. The customer
        must be logged in and the order must belong to them."""
        if not order_number:
            return {'success': False, 'error': 'order_number is required. Ask the customer for their order number.'}

        if user is None or not user.is_authenticated:
            return {'success': False, 'error': 'Customer is not logged in. Ask them to log in first.'}

        try:
            order = Order.objects.get(order_number=order_number, customer__user=user)
        except Order.DoesNotExist:
            return {'success': False, 'error': 'Order not found.'}

        if order.status == 'delivered':
            return {'success': False, 'error': 'Delivered orders cannot be cancelled.'}
        if order.status == 'cancelled':
            return {'success': False, 'error': 'Order is already cancelled.'}

        with transaction.atomic():
            for item in order.items.all():
                if item.product:
                    item.product.stock += item.quantity
                    item.product.save()
            order.status = 'cancelled'
            order.save()
            if hasattr(order, 'payment'):
                order.payment.status = 'refunded'
                order.payment.save()

        return {'success': True, 'order_number': order.order_number, 'status': order.status}

    return [add_to_cart, create_order, track_order, cancel_order]