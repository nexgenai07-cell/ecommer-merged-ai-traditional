# PATH: apps/ai/tools/cart_order_tools.py

# FLOW: shopping_agent.py se yahan aata hai (get_cart_order_tools call
# hoti hai). Ye tools DIRECT Django ORM use karte hain (Qdrant nahi,
# koi HTTP call nahi) — kyunke ye AI agent usi Django process ke andar
# chal raha hai jahan models available hain.

import logging   # NEW — payment-link generation ke errors log karne ke liye
from decimal import Decimal
from django.conf import settings   # NEW — STRIPE_SECRET_KEY / FRONTEND_URL ke liye
from django.db import transaction
from langchain_core.tools import tool

from apps.cart.models import Cart, CartItem, Wishlist     # FLOW → cart database tables
from apps.products.models import Product
from apps.stores.models import Store
from apps.orders.models import Customer, Order, OrderItem, Payment      # FLOW → order database tables
from apps.orders.views import generate_order_number
from typing import Optional

logger = logging.getLogger("ai.tools.cart_order_tools")   # NEW

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
    def get_cart() -> dict:
        """Show everything currently in the customer's shopping cart —
        each item with its name, price, quantity, and line total, plus the
        cart subtotal. Use this whenever the customer asks what's in their
        cart/basket, or before checkout to confirm what they're buying."""
        cart = _get_or_create_cart(user, session_key)
        items = list(cart.items.select_related('product').all())

        if not items:
            return {
                'success': True,
                'items': [],
                'total_items': 0,
                'subtotal': 0.0,
                'message': 'Cart is currently empty.',
            }

        item_list = []
        subtotal = Decimal('0')
        for i in items:
            line_total = i.product.price * i.quantity
            subtotal += line_total
            item_list.append({
                'product_id': i.product.id,
                'product_name': i.product.name,
                'price': float(i.product.price),
                'quantity': i.quantity,
                'line_total': float(line_total),
            })

        return {
            'success': True,
            'items': item_list,
            'total_items': sum(i.quantity for i in items),
            'subtotal': float(subtotal),
        }

    @tool
    def get_wishlist() -> dict:
        """Show everything currently in the customer's wishlist — each
        product's name, price, whether it's discounted (compare
        'original_price' vs 'price'), and whether it's in stock. Use this
        whenever the customer asks what's in their wishlist/favorites/saved
        items. Requires the customer to be logged in."""
        if user is None or not user.is_authenticated:
            return {'success': False, 'error': 'Customer is not logged in. Ask them to log in first to see their wishlist.'}

        wishlist = Wishlist.objects.filter(user=user).first()
        items = list(wishlist.items.select_related('product').all()) if wishlist else []

        if not items:
            return {'success': True, 'items': [], 'total_items': 0, 'message': 'Wishlist is currently empty.'}

        item_list = [
            {
                'product_id': i.product.id,
                'product_name': i.product.name,
                'price': float(i.product.price),
                'original_price': float(i.product.original_price) if i.product.original_price else None,
                'in_stock': i.product.in_stock,
                'stock': i.product.stock,
            }
            for i in items
        ]

        return {'success': True, 'items': item_list, 'total_items': len(item_list)}

    # NEW — CRITICAL FIX: is se pehle koi bhi tool nahi tha jo customer ki
    # request pe kisi product ko wishlist mein DAAL sake (sirf dekhne
    # wala get_wishlist tha). Is wajah se jab customer "wishlist mein add
    # karo" bolta tha, AI ke paas koi tareeqa hi nahi tha — aur na chahte
    # hue bhi tool ka naam ("add karne ka tool mere paas nahi hai") bol
    # deta tha (jo khud ek alag bug tha, system prompt mein fix kiya gaya
    # hai). Ab ye tool exist karta hai — get_wishlist jaisa hi pattern
    # (Wishlist.objects.filter(user=user) — bina store filter ke, bilkul
    # get_wishlist mein jaisa istemal ho raha hai).
    @tool
    def add_to_wishlist(product_id: int) -> dict:
        """Save a product to the customer's wishlist/favorites for later
        (as opposed to add_to_cart, which is for buying now). Use this
        whenever the customer says they want to save/wishlist/favorite a
        specific product. Requires the customer to be logged in."""
        if user is None or not user.is_authenticated:
            return {'success': False, 'error': 'Customer is not logged in. Ask them to log in first to use the wishlist.'}

        try:
            product = Product.objects.select_related('category').get(id=product_id, is_active=True)
        except Product.DoesNotExist:
            return {'success': False, 'error': 'Product not found.'}

        wishlist, _ = Wishlist.objects.get_or_create(user=user)
        item, created = wishlist.items.get_or_create(product=product)

        return {
            'success': True,
            'message': (
                f'{product.name} added to wishlist.' if created
                else f'{product.name} is already in your wishlist.'
            ),
            'product_id': product.id,
            'category_id': product.category.id if product.category else None,
            'product_name': product.name,
            'price': float(product.price),
        }

    @tool
    def remove_from_wishlist(product_id: int) -> dict:
        """Remove a product from the customer's wishlist/favorites. Use
        this whenever the customer wants a specific product taken off
        their wishlist. Requires the customer to be logged in."""
        if user is None or not user.is_authenticated:
            return {'success': False, 'error': 'Customer is not logged in. Ask them to log in first to use the wishlist.'}

        wishlist = Wishlist.objects.filter(user=user).first()
        if not wishlist:
            return {'success': False, 'error': 'Wishlist is currently empty.'}

        deleted_count, _ = wishlist.items.filter(product_id=product_id).delete()
        if not deleted_count:
            return {'success': False, 'error': 'That product is not in the wishlist.'}

        return {'success': True, 'message': 'Removed from wishlist.', 'product_id': product_id}

    @tool
    def list_my_orders(limit: int = 10) -> dict:
        """List the logged-in customer's own past orders — order number,
        status, and total amount for each. Use this whenever the customer
        asks about their order history, their order numbers, how many
        orders they've placed, or wants a list to pick from before asking
        about one specific order. Requires the customer to be logged in."""
        if limit is None:
            limit = 10

        if user is None or not user.is_authenticated:
            return {'success': False, 'error': 'Customer is not logged in. Ask them to log in first to see their order history.'}

        orders = list(Order.objects.filter(customer__user=user).order_by('-updated_at')[:limit])

        if not orders:
            return {'success': True, 'orders': [], 'total_found': 0, 'message': 'No orders found for this customer yet.'}

        order_list = [
            {
                'order_number': o.order_number,
                'status': o.status,
                'total_amount': float(o.total_amount),
                'discount_amount': float(o.discount_amount) if o.discount_amount else 0.0,
                'updated_at': str(o.updated_at),
            }
            for o in orders
        ]
        return {'success': True, 'orders': order_list, 'total_found': len(order_list)}

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

    # NEW — CRITICAL FEATURE: is se pehle koi tool nahi tha jo REAL payment
    # link bana sake — system prompt explicitly AI ko mana karta tha ke wo
    # khud koi link INVENT na kare (jo sahi tha, fake link se customer ka
    # trust tootta). Ab ye tool asal Stripe Checkout Session banata hai.
    #
    # ⚠️ ASSUMPTIONS FLAG — ye poora tool kuch cheezon ko assume karta hai
    # jo is `ai` app ke zip mein confirm nahi ho saki (Stripe integration
    # kahin aur, jaise apps/orders ya apps/payments mein ho sakti hai):
    #   1. `stripe` Python package installed hai (`pip install stripe`)
    #   2. settings.STRIPE_SECRET_KEY naam ki setting exist karti hai
    #   3. settings.FRONTEND_URL naam ki setting exist karti hai (agar
    #      nahi to neeche wala fallback URL istemal hoga — apna asal
    #      frontend domain confirm kar lena)
    #   4. Payment model mein 'stripe_session_id' field hai (agar nahi
    #      hai, koi crash nahi hoga — bas wo field silently skip ho
    #      jayegi, try/except se guarded hai)
    #   5. Cart currency PKR hai aur tumhara Stripe account PKR accept
    #      karta hai
    # Agar in mein se koi assumption galat nikle, mujhe apps/orders ya
    # apps/payments ka relevant code bhej dena — main exact fix de dungi.
    @tool
    def generate_payment_link(order_number: str) -> dict:
        """Generate a REAL, working Stripe Checkout payment link for an
        existing order that's pending payment. Use this right after an
        order is placed, or whenever the customer asks how to pay / asks
        for a payment link. order_number is REQUIRED. Customer must be
        logged in and the order must belong to them."""
        if not order_number:
            return {'success': False, 'error': 'order_number is required. Ask the customer for their order number.'}

        if user is None or not user.is_authenticated:
            return {'success': False, 'error': 'Customer is not logged in. Ask them to log in first before generating a payment link.'}

        try:
            order = Order.objects.select_related('payment').get(order_number=order_number, customer__user=user)
        except Order.DoesNotExist:
            return {'success': False, 'error': 'Order not found.'}

        if hasattr(order, 'payment') and order.payment.status == 'paid':
            return {'success': False, 'error': 'This order has already been paid for.'}

        try:
            import stripe   # NEW — local import taake package na hone ki soorat mein sirf isi tool ka istemal fail ho, poori file nahi
            stripe.api_key = settings.STRIPE_SECRET_KEY

            frontend_url = getattr(settings, 'FRONTEND_URL', 'https://ecommerce-frontend-n7h2.vercel.app').rstrip('/')

            session = stripe.checkout.Session.create(
                mode='payment',
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': 'pkr',
                        'product_data': {'name': f'Order {order.order_number}'},
                        'unit_amount': int(round(float(order.total_amount) * 100)),  # Stripe smallest-unit (paisa) mein leta hai
                    },
                    'quantity': 1,
                }],
                success_url=f'{frontend_url}/orders/{order.order_number}?payment=success',
                cancel_url=f'{frontend_url}/orders/{order.order_number}?payment=cancelled',
                metadata={'order_number': order.order_number},
            )

            # NEW — session id save karna optional hai, agar field exist
            # nahi karti to silently skip (guarded, crash nahi hoga)
            try:
                if hasattr(order, 'payment') and hasattr(order.payment, 'stripe_session_id'):
                    order.payment.stripe_session_id = session.id
                    order.payment.save(update_fields=['stripe_session_id'])
            except Exception:
                logger.exception("[generate_payment_link] couldn't save stripe_session_id for order=%s (non-fatal)", order_number)

            return {
                'success': True,
                'payment_url': session.url,
                'order_number': order.order_number,
                'amount': float(order.total_amount),
            }
        except Exception as e:
            logger.exception("[generate_payment_link] failed for order=%s", order_number)
            return {'success': False, 'error': f'Could not generate payment link right now: {str(e)}'}

    @tool
    def track_order(order_number: str) -> dict:
        """Get the full details of an existing order — status, tracking
        number, total amount paid, discount applied, payment status, and
        the list of items in it. Use this for ANY question about a specific
        order, including status, tracking, amount paid, or discount.
        order_number is REQUIRED — if the customer hasn't given their order
        number yet, ask them for it before calling this tool. The customer
        must also be logged in, and the order must belong to them."""
        if not order_number:
            return {'success': False, 'error': 'order_number is required. Ask the customer for their order number.'}

        if user is None or not user.is_authenticated:
            return {'success': False, 'error': 'Customer is not logged in. Ask them to log in first before tracking an order.'}

        try:
            order = Order.objects.select_related('payment').prefetch_related('items').get(
                order_number=order_number, customer__user=user,
            )
        except Order.DoesNotExist:
            return {'success': False, 'error': 'Order not found.'}

        items = [
            {
                'product_name': it.product_name,
                'quantity': it.quantity,
                'price': float(it.price),
                'total_price': float(it.total_price),
            }
            for it in order.items.all()
        ]

        subtotal = float(order.total_amount) + (float(order.discount_amount) if order.discount_amount else 0.0)

        return {
            'success': True,
            'order_number': order.order_number,
            'status': order.status,
            'tracking_number': order.tracking_number,
            'updated_at': str(order.updated_at),
            'subtotal': subtotal,
            'discount_amount': float(order.discount_amount) if order.discount_amount else 0.0,
            'total_amount': float(order.total_amount),
            'payment_status': order.payment.status if hasattr(order, 'payment') else None,
            'items': items,
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

    return [
        add_to_cart, get_cart, get_wishlist, add_to_wishlist, remove_from_wishlist,
        create_order, list_my_orders, track_order, cancel_order, generate_payment_link,
    ]