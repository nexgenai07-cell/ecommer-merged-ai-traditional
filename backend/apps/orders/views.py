# PATH: apps/orders/views.py

import logging
import threading

import stripe
from decimal import Decimal
from apps.ai.audit import log_manual_admin_action as log_admin_action
from django.conf import settings
import random
import string

stripe.api_key = settings.STRIPE_SECRET_KEY

logger = logging.getLogger(__name__)

from django.db.models import Q, F, ExpressionWrapper, IntegerField
from django.db import transaction, IntegrityError
from django.utils import timezone

from rest_framework import status, permissions, generics
from rest_framework.views import APIView
from rest_framework.response import Response

from core.pagination import StandardResultsPagination
from apps.notifications.utils import (
    create_notification,
    notify_store_admins,
    send_order_confirmation_email,
    send_refund_confirmation_email,
)

from .models import Address, CheckoutOTP, Customer, Order, OrderItem, Payment
from .serializers import (
    OrderListSerializer,
    AdminOrderListSerializer,
    OrderDetailSerializer,
    CheckoutSerializer,
    CheckoutPrefillSerializer,
    SaveAddressSerializer,
    AdminOrderStatusSerializer,
)

from apps.cart.models import Cart
from apps.products.models import Product, StockMovement
from apps.products.services import check_low_stock_notification
from apps.stores.models import Store
from apps.users.permissions import IsAdmin, IsCustomer

def generate_order_number(): # Generates a unique order number for every new order.
    year = timezone.now().year
    last_order = (
        Order.objects.filter(order_number__startswith=f"ORD-{year}-")
        .order_by("-id")
        .first()
    )

    if last_order:
        last_seq = int(last_order.order_number.split("-")[-1])
        new_seq = last_seq + 1
    else:
        new_seq = 1

    return f"ORD-{year}-{new_seq:05d}"

# Finds an existing customer profile or creates one for the current user.
def get_or_create_customer(user, store_id=1):
    try:
        customer, _ = Customer.objects.get_or_create(
            user=user,
            store_id=store_id,
            defaults={
                "name": user.name,
                "phone": user.phone or "",
                "email": user.email,
            },
        )
    except IntegrityError:
        # NEW (Sep 2026 — defensive safety net): the (phone, store)
        # blank-phone collision this used to hit is already fixed at the
        # database level (see Customer.Meta.constraints in models.py —
        # blank phone no longer participates in that uniqueness check).
        # This catch only remains for the genuine, much rarer case of
        # two simultaneous requests for the SAME user+store both racing
        # get_or_create() at once (not something a blank phone can cause
        # anymore) — one of them wins the insert, the other lands here
        # and just needs to re-fetch the row the first one created.
        customer = Customer.objects.get(user=user, store_id=store_id)
    return customer


# NEW (Buy Now): minimal stand-in for a real CartItem, used only by
# CheckoutView's Buy Now branch below. Exposes exactly the attributes
# (product, product_id, quantity) that the checkout logic below already
# reads off a real CartItem, so every downstream step — stock locking,
# subtotal calculation, OrderItem creation, stock reservation — runs
# completely unchanged whether the order came from the persisted cart or
# a single Buy Now click.
class _BuyNowItem:
    def __init__(self, product, quantity):
        self.product = product
        self.product_id = product.id
        self.quantity = quantity


def reserve_stock_for_order(order):
    """
    Transition 1: Checkout (API 55), order created as pending_payment
    reserved_stock += qty, total_stock unchanged
    """
    if order.stock_deducted:
        return

    items = list(order.items.select_related("product").all())
    product_ids = [item.product_id for item in items if item.product_id]

    if product_ids:
        locked_products = {
            p.id: p
            for p in Product.objects.select_for_update().filter(id__in=product_ids)
        }

        for item in items:
            product = locked_products.get(item.product_id)
            if not product:
                continue

            old_reserved = product.reserved_stock
            new_reserved = old_reserved + item.quantity

            if new_reserved > product.total_stock:
                raise Exception(
                    f"Cannot reserve {item.quantity} units for product {product.name}. "
                    f"Available stock: {product.total_stock - old_reserved}"
                )

            # NEW (Notification Triggers Addendum, Item 17): this is the
            # point where available_stock (total_stock - reserved_stock)
            # actually decreases in this codebase — total_stock itself
            # only changes later, at payment confirmation, when
            # reserved_stock drops by the same amount total_stock does
            # (net available_stock change = 0 at that point). Snapshot
            # before/after here so the low-stock crossing check fires at
            # the moment it's technically true, not just at confirmation.
            old_available = product.total_stock - old_reserved
            new_available = product.total_stock - new_reserved

            product.reserved_stock = new_reserved
            product.save(update_fields=["reserved_stock"])

            StockMovement.objects.create(
                product=product,
                changed_by=None,
                old_stock=product.total_stock,
                new_stock=product.total_stock,
                delta=0,
                reason="order_placed",
                note=f"Order {order.order_number} - reserved {item.quantity} units",
            )

            check_low_stock_notification(product, old_available, new_available)


def release_reserved_stock_for_order(order):
    """
    Transition 3: Order cancelled (customer API 58, admin API 63)
    OR payment timeout.

    If payment was never confirmed:
        reserved_stock -= qty
        total_stock unchanged

    If payment was confirmed:
        total_stock += qty
        reserved_stock -= qty
    """
    if not order.stock_deducted:
        items = list(order.items.select_related("product").all())
        product_ids = [item.product_id for item in items if item.product_id]

        if product_ids:
            locked_products = {
                p.id: p
                for p in Product.objects.select_for_update().filter(id__in=product_ids)
            }

            for item in items:
                product = locked_products.get(item.product_id)
                if not product:
                    continue

                old_reserved = product.reserved_stock
                new_reserved = max(old_reserved - item.quantity, 0)

                product.reserved_stock = new_reserved
                product.save(update_fields=["reserved_stock"])

                StockMovement.objects.create(
                    product=product,
                    changed_by=None,
                    old_stock=product.total_stock,
                    new_stock=product.total_stock,
                    delta=0,
                    reason="order_cancelled",
                    note=f"Order {order.order_number} - released {item.quantity} units",
                )

        return

    items = list(order.items.select_related("product").all())
    product_ids = [item.product_id for item in items if item.product_id]

    if product_ids:
        locked_products = {
            p.id: p
            for p in Product.objects.select_for_update().filter(id__in=product_ids)
        }

        for item in items:
            product = locked_products.get(item.product_id)
            if not product:
                continue

            old_total = product.total_stock
            new_total = old_total + item.quantity

            old_reserved = product.reserved_stock
            new_reserved = max(old_reserved - item.quantity, 0)

            product.total_stock = new_total
            product.reserved_stock = new_reserved
            product.save(update_fields=["total_stock", "reserved_stock"])

            StockMovement.objects.create(
                product=product,
                changed_by=None,
                old_stock=old_total,
                new_stock=new_total,
                delta=item.quantity,
                reason="order_cancelled",
                note=f"Order {order.order_number} cancelled - restored {item.quantity} units",
            )

    order.stock_deducted = False

# Restores stock for all products when an order is cancelled.
def restore_stock_for_order(order, user=None):
    """
    Shared helper — restores stock for every item in a cancelled order,
    using the same select_for_update() + StockMovement audit pattern as
    checkout and the manual adjust endpoint (stock race-condition fix,
    point 6: cancellations must use the same atomic approach and be
    logged in the same audit trail).

    'user' is the admin who triggered the status change, if any — None
    means the cancellation was triggered by the customer themselves via
    OrderCancelView, which is still a real actor (request.user), so
    callers should pass request.user; this stays None only if truly
    system-triggered elsewhere in the future.

    FIX (B59): stock is only ever deducted once payment is confirmed (see
    deduct_stock_for_order below), so restoring stock only makes sense —
    and must only happen — if this specific order actually had stock
    deducted in the first place. Without this guard, cancelling an unpaid
    order (which never touched stock) would incorrectly ADD stock back
    that was never removed. order.stock_deducted is the single source of
    truth for this and gets flipped back to False here; callers still do
    their own order.save() right after, which persists it.
    """
    if not order.stock_deducted:
        return

    items = list(order.items.select_related("product").all())
    product_ids = [item.product_id for item in items if item.product_id]

    if product_ids:
        locked_products = {
            p.id: p
            for p in Product.objects.select_for_update().filter(id__in=product_ids)
        }

        for item in items:
            product = locked_products.get(item.product_id)
            if not product:
                continue

            old_stock = product.stock
            new_stock = old_stock + item.quantity

            product.stock = new_stock
            product.save(update_fields=["stock"])

            StockMovement.objects.create(
                product=product,
                changed_by=user,
                old_stock=old_stock,
                new_stock=new_stock,
                delta=item.quantity,
                reason="order_cancelled",
                note=f"Order {order.order_number} cancelled",
            )

    order.stock_deducted = False


# NEW (B59): stock now only leaves inventory once payment is actually
# confirmed — not at checkout/pending_payment time. Called from
# payments/views.py at the two places an order genuinely becomes paid:
# CreatePaymentIntentView's free-order (Rs. 0) branch, and
# StripeWebhookView's payment_intent.succeeded handler.
def deduct_stock_for_order(order, user=None):
    """
    Transition 2: Payment confirmed.

    reserved_stock -= quantity
    total_stock -= quantity

    This is idempotent through order.stock_deducted, so a duplicate
    Stripe webhook cannot deduct stock twice.
    """
    if order.stock_deducted:
        return

    items = list(order.items.select_related("product").all())
    product_ids = [item.product_id for item in items if item.product_id]

    if product_ids:
        locked_products = {
            p.id: p
            for p in Product.objects.select_for_update().filter(
                id__in=product_ids
            )
        }

        for item in items:
            product = locked_products.get(item.product_id)
            if not product:
                continue

            old_total = product.total_stock
            old_reserved = product.reserved_stock

            # The quantity was reserved during checkout.
            # Now payment is confirmed, so it becomes permanently sold.
            new_total = max(old_total - item.quantity, 0)
            new_reserved = max(old_reserved - item.quantity, 0)

            product.total_stock = new_total
            product.reserved_stock = new_reserved

            product.save(
                update_fields=[
                    "total_stock",
                    "reserved_stock",
                ]
            )

            StockMovement.objects.create(
                product=product,
                changed_by=user,
                old_stock=old_total,
                new_stock=new_total,
                delta=new_total - old_total,
                reason="order_confirmed",
                note=(
                    f"Order {order.order_number} payment confirmed - "
                    f"deducted {item.quantity} units"
                ),
            )

    order.stock_deducted = True
    order.save(update_fields=["stock_deducted"])
# Backward-compatible name used by older payment code.
# Stock confirmation now uses the same safe deduction helper.
def confirm_stock_for_order(order, user=None):
    return deduct_stock_for_order(order, user=user)


# NEW (B27): when an admin cancels an order, suggest in-stock alternatives
# for any item that was out of stock.
#
# FIX (Frontend Clarification — Suggested Alternatives, Sep 2026):
#   - Ask 1: field was silently dropped from the response whenever there
#     was nothing to suggest (see AdminOrderStatusUpdateView.put below —
#     it used to only attach the key when the dict was non-empty). The
#     key is now always attached to the response on cancellation, as an
#     empty list if there's nothing to suggest, so the frontend can rely
#     on it always being present.
#   - Ask 2 (logic, pending final backend-team confirmation, current
#     behaviour documented here so it can be confirmed/adjusted):
#       * Trigger — per line item, not based on matching the
#         cancellation_reason string. A suggestion is generated for any
#         item whose product currently has available_stock <= 0. This is
#         intentionally reason-agnostic since cancellation_reason is a
#         free-text field an admin can phrase however they like ("out of
#         stock", "OOS", "no stock left", etc.) and matching on exact/
#         keyword text would be fragile.
#       * Matching — same category as the out-of-stock product (falls
#         back to any in-stock product if the item has no category),
#         currently active, not soft-deleted, and available_stock > 0.
#       * Limit — top 3 alternatives per out-of-stock item. If more than
#         one item in the order is out of stock, the overall list can
#         contain more than 3 entries in total, but each product id only
#         appears once (deduped) even if it would qualify for multiple
#         items.
#       * Field shape — flat list of objects, matching what the frontend
#         asked for: id, name, primary_image, price, available_stock.
def suggest_alternatives_for_order(order):
    suggestions = []
    seen_ids = set()

    for item in order.items.select_related("product", "product__category").all():
        product = item.product
        if not product or product.available_stock > 0:
            continue

        alternatives_qs = (
            Product.objects.filter(is_delete=False, is_active=True)
            .exclude(id=product.id)
            .annotate(
                computed_available_stock=ExpressionWrapper(
                    F("total_stock") - F("reserved_stock"),
                    output_field=IntegerField(),
                )
            )
            .filter(computed_available_stock__gt=0)
            .prefetch_related("images")
        )

        if product.category_id:
            alternatives_qs = alternatives_qs.filter(category_id=product.category_id)

        for alt in alternatives_qs.order_by("-created_at")[:3]:
            if alt.id in seen_ids:
                continue

            images = list(alt.images.all())
            primary = next((img for img in images if img.is_primary), None)
            img = primary or (images[0] if images else None)
            primary_image_url = (
                img.image.url.replace("http://", "https://")
                if img and img.image
                else None
            )

            suggestions.append(
                {
                    "id": alt.id,
                    "name": alt.name,
                    "primary_image": primary_image_url,
                    "price": str(alt.price),
                    "available_stock": alt.computed_available_stock,
                }
            )
            seen_ids.add(alt.id)

    return suggestions

# Handles checkout by creating an order, validating stock and creating payment.
# Handles checkout by creating an order, validating stock and creating payment.
class CheckoutView(APIView):
    # FIX (B43): only customers can checkout.
    # Admin users must not be allowed to create customer orders.
    permission_classes = [permissions.IsAuthenticated, IsCustomer]

    # Validates cart, creates order, and creates a pending payment.
    # Stock is reserved at checkout but is only deducted from total_stock
    # after payment is actually confirmed.
    def post(self, request):
        serializer = CheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # NEW (Buy Now): if buy_now_product_id is present, this checkout
        # is for ONE product clicked straight from the product detail
        # page — the persisted cart is never read or modified in this
        # branch, so the customer's real cart survives a Buy Now
        # purchase completely untouched.
        buy_now_product_id = data.get("buy_now_product_id")
        is_buy_now = bool(buy_now_product_id)

        cart = Cart.objects.filter(user=request.user).first()

        if is_buy_now:
            try:
                buy_now_product = Product.objects.get(
                    id=buy_now_product_id,
                    is_active=True,
                    is_delete=False,
                )
            except Product.DoesNotExist:
                return Response(
                    {"error": "Product not found."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            checkout_store_id = buy_now_product.store_id
            # Buy Now does not apply whatever coupon happens to be on the
            # customer's cart — it's a separate, one-off purchase.
            checkout_coupon = None
        else:
            if not cart or not cart.items.exists():
                return Response(
                    {"error": "Your cart is empty."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            checkout_store_id = cart.store_id
            checkout_coupon = cart.coupon

        # Resolve the customer profile first.
        customer = get_or_create_customer(
            request.user,
            store_id=checkout_store_id,
        )

        # ============================================================
        # ADDRESS RESOLUTION
        # Priority:
        # 1. address_id from Address Book
        # 2. manually supplied address fields
        # 3. customer's default Address Book address
        # ============================================================

        address_id = data.get("address_id")
        selected_address = None

        if address_id:
            selected_address = Address.objects.filter(
                id=address_id,
                customer=customer,
            ).first()

            if not selected_address:
                return Response(
                    {"error": "Address not found."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if selected_address:
            # Customer selected an existing Address Book entry.
            shipping_address = selected_address.shipping_address
            city = selected_address.city
            postal_code = selected_address.postal_code or ""
            contact_phone = selected_address.phone or ""

        else:
            # No address_id: use manually supplied checkout fields.
            shipping_address = data.get("shipping_address") or ""
            city = data.get("city") or ""
            postal_code = data.get("postal_code") or ""
            contact_phone = data.get("phone") or ""

            # If manual shipping address/city is incomplete,
            # fall back to the customer's default saved address.
            if not shipping_address.strip() or not city.strip():
                default_address = Address.objects.filter(
                    customer=customer,
                    is_default=True,
                ).first()

                if default_address:
                    shipping_address = default_address.shipping_address
                    city = default_address.city
                    postal_code = default_address.postal_code or ""
                    contact_phone = default_address.phone or ""

        # shipping_address and city are required.
        # postal_code and phone remain optional.
        missing = []

        if not shipping_address.strip():
            missing.append("shipping_address")

        if not city.strip():
            missing.append("city")

        if missing:
            return Response(
                {
                    "error": (
                        "Please provide your shipping details before "
                        f"checking out. Missing: {', '.join(missing)}."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ============================================================
        # NEW (Sep 2026 — Checkout OTP verification): block order
        # creation until the customer has requested AND verified the
        # code emailed to their account address (see otp_views.py).
        # A verification is single-use — consumed further down, right
        # after the order is actually created — so placing another
        # order later needs a fresh send-otp/verify-otp round trip.
        # ============================================================
        checkout_otp = CheckoutOTP.objects.filter(user=request.user).first()

        if not checkout_otp or not checkout_otp.is_verification_usable():
            return Response(
                {
                    "error": (
                        "Please verify the code sent to your email before "
                        "placing the order."
                    ),
                    "otp_required": True,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():

            # NEW (Buy Now): a single synthetic item instead of the
            # cart's real items — see _BuyNowItem above. Everything below
            # this point (stock locking, subtotal, order/OrderItem
            # creation, stock reservation) runs unchanged either way.
            if is_buy_now:
                cart_items = [
                    _BuyNowItem(
                        product=buy_now_product,
                        quantity=data.get("buy_now_quantity") or 1,
                    )
                ]
            else:
                cart_items = list(
                    cart.items.select_related("product").all()
                )

            product_ids = [
                item.product_id
                for item in cart_items
                if item.product_id
            ]

            # Lock Product rows so concurrent checkouts cannot both
            # read the same stale stock values.
            locked_products = {
                p.id: p
                for p in Product.objects.select_for_update().filter(
                    id__in=product_ids
                )
            }

            out_of_stock = []

            for item in cart_items:
                product = locked_products.get(item.product_id)

                # Available stock = total stock minus already reserved stock.
                available = (
                    product.total_stock - product.reserved_stock
                    if product
                    else 0
                )

                if not product or available < item.quantity:
                    out_of_stock.append(
                        item.product.name
                        if item.product
                        else "Unknown product"
                    )

            if out_of_stock:
                return Response(
                    {
                        "error": (
                            "These items are no longer available "
                            f"in the requested quantity: "
                            f"{', '.join(out_of_stock)}"
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # ============================================================
            # CALCULATE ORDER TOTAL
            # ============================================================

            subtotal = sum(
                item.product.price * item.quantity
                for item in cart_items
            )

            discount_amount = 0

            if checkout_coupon:
                if checkout_coupon.type == "percent":
                    discount_amount = (
                        subtotal * checkout_coupon.value
                    ) / 100
                else:
                    discount_amount = checkout_coupon.value

                discount_amount = min(
                    discount_amount,
                    subtotal,
                )

            # NEW (Shipping cost fix — Sep 2026): shipping_method is
            # required by CheckoutSerializer ("standard" or "express").
            # shipping_cost is derived from it here (never taken from the
            # client) and added to total_amount, same as it goes into the
            # Payment record and, for Stripe orders, the actual Stripe
            # PaymentIntent amount (payments/views.py:CreatePaymentIntentView
            # just uses order.total_amount, so it picks this up
            # automatically). Applies identically to both Stripe and QR
            # checkouts.
            shipping_method = data["shipping_method"]
            shipping_cost = Order.SHIPPING_COSTS[shipping_method]

            total_amount = subtotal - discount_amount + shipping_cost

            # ============================================================
            # CREATE ORDER
            # ============================================================

            order = Order.objects.create(
                store_id=checkout_store_id,
                customer=customer,
                order_number=generate_order_number(),
                total_amount=total_amount,
                discount_amount=discount_amount,
                shipping_method=shipping_method,
                shipping_cost=shipping_cost,
                status="pending_payment",
                shipping_address=shipping_address,
                city=city,
                postal_code=postal_code,
                contact_phone=contact_phone,
                notes=data.get("notes", ""),
            )

            # Spend this verification now that the order actually exists,
            # so it can't be reused to authorize a second order.
            checkout_otp.consumed_at = timezone.now()
            checkout_otp.save(update_fields=["consumed_at", "updated_at"])

            # ============================================================
            # CREATE ORDER ITEMS
            # ============================================================

            for item in cart_items:
                OrderItem.objects.create(
                    order=order,
                    product=item.product,
                    product_name=item.product.name,
                    price=item.product.price,
                    quantity=item.quantity,
                    total_price=item.product.price * item.quantity,
                )

            # ============================================================
            # RESERVE STOCK
            #
            # Checkout:
            # reserved_stock += quantity
            # total_stock remains unchanged.
            #
            # Actual total_stock deduction happens only after payment
            # confirmation.
            # ============================================================

            reserve_stock_for_order(order)

            # ============================================================
            # CREATE PAYMENT
            # ============================================================

            payment_method = data["payment_method"]

            Payment.objects.create(
                order=order,
                status="pending",
                amount=total_amount,
                payment_method=payment_method,
            )

            # ============================================================
            # CLEAR CART
            #
            # NEW (Buy Now): skipped entirely — a Buy Now purchase never
            # read from the persisted cart, so there is nothing on it to
            # clear here, and the customer's actual cart is left exactly
            # as it was before they clicked Buy Now.
            # ============================================================

            if not is_buy_now and cart:
                cart.items.all().delete()
                cart.coupon = None
                cart.save()

        # ================================================================
        # NOTIFICATION
        # ================================================================
        # FIX (Cross-check, checkout crash — Sep 2026): order + payment are
        # already committed above by this point. A notification is a
        # non-critical side effect and must never be allowed to break the
        # checkout response — so this is wrapped in try/except, and this
        # order's own store is passed explicitly instead of relying on
        # create_notification()'s Store.objects.first() fallback (which
        # could pick the wrong store, or be None and crash).

        try:
            create_notification(
                user=request.user,
                title="Order placed",
                message=(
                    f"Your order {order.order_number} has been placed "
                    "and is awaiting payment."
                ),
                notification_type="order",
                reference_type="order",
                reference_id=order.order_number,
                store=order.store,
            )
        except Exception:
            logger.exception(
                "CheckoutView: create_notification failed for order %s",
                order.order_number,
            )

        # NEW (Notification Triggers Addendum, Item 12): "New order
        # placed" — every admin of the store must also be notified,
        # separately from the customer notification above.
        # UPDATED (v4.0): store.owner removed — routed to all of the
        # store's admins via notify_store_admins() instead. Same
        # non-critical-side-effect handling: never allowed to break the
        # checkout response.
        try:
            notify_store_admins(
                order.store,
                title="New order received",
                message=(
                    f"Order {order.order_number} has been placed and is "
                    "awaiting processing."
                ),
                notification_type="order",
                reference_type="order",
                reference_id=order.order_number,
            )
        except Exception:
            logger.exception(
                "CheckoutView: admin create_notification failed for order %s",
                order.order_number,
            )

        # ================================================================
        # EMAIL
        # ================================================================
        # FIX (Cross-check, checkout crash — Sep 2026), UPDATED after
        # Railway logs confirmed the real failure mode: this was never a
        # normal Python exception — send_order_confirmation_email() already
        # catches and logs its own errors internally (see the "failed to
        # send email" log line in apps/notifications/utils.py), so it never
        # raised anything for a try/except here to catch.
        #
        # The real problem is a genuine BLOCKING network hang: Railway's
        # outbound network can't reach Gmail's SMTP host at all (OSError:
        # Network is unreachable), and the socket connect attempt blocks
        # this request's own thread for 60+ seconds before it errors out.
        # Daphne (the ASGI server) has its own per-request timeout and
        # force-kills the whole connection once it decides the request
        # "took too long to shut down" — and it does this BEFORE the SMTP
        # call ever returns, which is exactly why the order saves but the
        # client gets zero response headers, and why the "failed to send
        # email" log line only appears several seconds AFTER Daphne's kill
        # warning (the OS thread was still stuck inside socket.connect()
        # when Daphne gave up on it).
        #
        # Fix: never let this request's own thread block on it. Fire the
        # email in a separate background thread and return the checkout
        # response immediately, regardless of whether the email eventually
        # succeeds, hangs, or errors — send_order_confirmation_email's own
        # internal error handling still applies, this just takes it off
        # the response's critical path entirely.

        threading.Thread(
            target=send_order_confirmation_email,
            args=(order,),
            daemon=True,
        ).start()

        # ================================================================
        # RESPONSE
        # ================================================================

        response_data = OrderDetailSerializer(order).data

        # QR payment requires the configured static QR image URL.
        # FIX (Cross-check, Sep 2026 — frontend report on ORD-2026-00062):
        # "payment_reference" was specced (v2 doc, Part 3) alongside
        # qr_image_url for QR checkouts, but only qr_image_url was ever
        # added here — payment_reference was missing from the response
        # entirely. Per spec its value is just the order's own
        # order_number (the string customers write in the bank transfer
        # note), not a separately generated code.
        if payment_method == "qr":
            response_data["qr_image_url"] = getattr(
                settings,
                "QR_PAYMENT_IMAGE_URL",
                "",
            )
            response_data["payment_reference"] = order.order_number

        return Response(
            response_data,
            status=status.HTTP_201_CREATED,
        )
# NEW (F8): lets the frontend prefill the checkout form with whatever the
# customer already has saved, instead of asking them to retype everything.
class CheckoutPrefillView(APIView):
    """GET /api/v1/orders/checkout/prefill/"""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        customer = Customer.objects.filter(user=request.user).first()

        data = {
            "shipping_address": customer.address if customer else "",
            "city": customer.city if customer else "",
            "postal_code": customer.postal_code if customer else "",
            "phone": (customer.phone if customer else "") or request.user.phone or "",
        }
        return Response(CheckoutPrefillSerializer(data).data)


# NEW (B22): "Save Address" as its own action, independent of checkout —
# fixes it being non-functional by giving it a real endpoint to call.
class SaveAddressView(APIView):
    """PUT /api/v1/orders/save-address/"""
    permission_classes = [permissions.IsAuthenticated]

    def put(self, request):
        serializer = SaveAddressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        store = Store.objects.first()
        customer = get_or_create_customer(request.user, store_id=store.id if store else 1)

        customer.address = data["shipping_address"]
        customer.city = data["city"]
        customer.postal_code = data.get("postal_code", customer.postal_code)
        if data.get("phone"):
            customer.phone = data["phone"]
        customer.save(
            update_fields=["address", "city", "postal_code", "phone", "updated_at"]
        )

        return Response(
            {
                "message": "Address saved.",
                "shipping_address": customer.address,
                "city": customer.city,
                "postal_code": customer.postal_code,
                "phone": customer.phone,
            }
        )


# Returns all orders belonging to the logged-in customer.
class OrderListView(generics.ListAPIView):
    """GET /api/v1/orders/

    FIX (B32): supports optional start_date / end_date query params (e.g.
    ?start_date=2026-08-01&end_date=2026-08-25) so customers can filter
    their own order history by date range, mirroring what admins already
    had via AdminOrderFilterView.

    FIX (Frontend audit, Sep 2026): also accepts an optional `status`
    query param (e.g. ?status=delivered), so the customer's status-tab
    filter (Pending/Confirmed/Shipped/Delivered/Cancelled) is applied
    server-side instead of the frontend fetching every page and
    filtering in the browser. Mirrors AdminOrderFilterView's status
    handling, including the same "pending" -> "pending_payment" alias.
    """
    serializer_class = OrderListSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsPagination

    def get_queryset(self): # Fetches customer order history.
        qs = (
            Order.objects.filter(
                customer__user=self.request.user
            )
            .order_by("-created_at")
        )

        params = self.request.query_params

        status_param = params.get("status")
        if status_param:
            # Same alias as AdminOrderFilterView: "pending" isn't a real
            # Order.status value in this schema — the only pending-type
            # order status is "pending_payment".
            if status_param == "pending":
                status_param = "pending_payment"
            qs = qs.filter(status=status_param)

        start_date = params.get("start_date")
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)

        end_date = params.get("end_date")
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)

        return qs

# Returns complete details of a single order.
class OrderDetailView(generics.RetrieveAPIView):
    serializer_class = OrderDetailSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "order_number"

# Restricts customers to their own orders while allowing admins to view all.
    queryset = (
        Order.objects
        .select_related("customer", "store", "payment")
        .prefetch_related("items")
    )

    def get_queryset(self):
        if self.request.user.is_staff:
            return self.queryset

        return self.queryset.filter(
            customer__user=self.request.user
        )
        
        
# Allows a customer to cancel an order.
class OrderCancelView(APIView):
    """PUT /api/v1/orders/{order_number}/cancel/"""
    permission_classes = [permissions.IsAuthenticated]

# Cancels order, restores stock (if any was deducted) and updates payment.
    def put(self, request, order_number):
        try:
            order = Order.objects.get(
                order_number=order_number,
                customer__user=request.user,
            )
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # UPDATED (Supervisor Scenario 12, Sep 2026): a customer can only
        # cancel an order *before* it has shipped. Once it's shipped,
        # out for delivery, or delivered, the package is already
        # physically moving/moved — the customer can no longer cancel it
        # themselves (an admin can still handle it as a return instead).
        if order.status in ("shipped", "out_for_delivery", "delivered"):
            return Response(
                {
                    "error": (
                        f"This order has already been {order.status.replace('_', ' ')} "
                        "and can no longer be cancelled."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if order.status == "cancelled":
            return Response(
                {"error": "Order is already cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # FIX (Cross-check, Sep 2026): this used to call the old,
            # dead restore_stock_for_order() helper, which operates on a
            # plain product.stock field that no longer exists on this
            # schema (Product now uses total_stock/reserved_stock) — it
            # would either crash or silently leave stock accounting wrong.
            # release_reserved_stock_for_order() is the correct helper for
            # Transition 3 (order cancelled) under the reserved-stock
            # system.
            release_reserved_stock_for_order(order)

            order.status = "cancelled"
            order.save()

            # FIX (B29): refunded_at timestamp gives a real, checkable
            # confirmation that the refund was processed, instead of just
            # a silent status flip.
            was_paid = False
            if hasattr(order, "payment"):
                was_paid = order.payment.status == "paid"
                order.payment.status = "refunded"
                order.payment.refunded_at = timezone.now()
                order.payment.save()

        # FIX (B28): customer-initiated cancellation previously created no
        # notification at all, unlike the admin-initiated path — so the
        # customer had nothing confirming the cancellation actually
        # registered on their side.
        # FIX (Notification Triggers Addendum, Item 16 cross-check): this
        # call was missing store=order.store, so in a multi-store setup
        # create_notification()'s fallback (Store.objects.first()) could
        # tag it to the wrong store. Passing it explicitly, same as every
        # other caller in this file.
        create_notification(
            user=request.user,
            store=order.store,
            title="Order cancelled",
            message=f"Your order {order.order_number} has been cancelled.",
            notification_type="order",
            reference_type="order",
            reference_id=order.order_number,
        )

        # NEW (Notification Triggers Addendum, Item 16): "Order cancelled
        # by customer" — every admin of the store must also be notified,
        # since it affects their stock/fulfillment. This is IN ADDITION to
        # the customer-facing notification above; both fire on this same
        # call.
        # UPDATED (v4.0): store.owner removed — routed to all of the
        # store's admins via notify_store_admins() instead.
        notify_store_admins(
            order.store,
            title="Order cancelled by customer",
            message=f"Order {order.order_number} was cancelled by the customer.",
            notification_type="order",
            reference_type="order",
            reference_id=order.order_number,
        )

        if was_paid:
            send_refund_confirmation_email(order)

        return Response(OrderDetailSerializer(order).data)

# Returns tracking information for an order.
class OrderTrackView(APIView):
    """GET /api/v1/orders/{order_number}/track/"""
    permission_classes = [permissions.IsAuthenticated]

# Fetches current order status and tracking number.
    def get(self, request, order_number):
        try:
            if request.user.is_staff:
                # Admin can view any order
                order = Order.objects.get(order_number=order_number)
            else:
                # Customer can only view their own order
                order = Order.objects.get(
                    order_number=order_number,
                    customer__user=request.user,
                )

        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "order_number": order.order_number,
                "status": order.status,
                "tracking_number": order.tracking_number,
                "updated_at": order.updated_at,
            },
            status=status.HTTP_200_OK,
        )

# ============================================================
# ADMIN VIEWS
# ============================================================

# Returns all orders for administrators.
class AdminOrderListView(generics.ListAPIView):
    """GET /api/v1/admin/orders/"""
    serializer_class = AdminOrderListSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    pagination_class = StandardResultsPagination

# Retrieves every order in the system.
    def get_queryset(self):
        return Order.objects.all().order_by("-created_at")

# Allows admins to update order status.
class AdminOrderStatusUpdateView(APIView):
    """PUT /api/v1/admin/orders/{order_number}/status/"""
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

# Updates order status, restores stock if cancelled and notifies customer.
    def put(self, request, order_number):
        try:
            order = Order.objects.get(order_number=order_number)
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = AdminOrderStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # FIX (Admin dashboard bug report, Sep 2026): once an order's
        # payment has been refunded OR its QR proof was rejected, the
        # order's story is over — no further status changes are allowed
        # via this endpoint at all (any new_status, not just a specific
        # one). The only way back for a refunded/rejected/cancelled order
        # is the dedicated Reinstate endpoint, which explicitly resets
        # the payment record first.
        existing_payment = getattr(order, "payment", None)
        if existing_payment and existing_payment.status in ("refunded", "rejected"):
            return Response(
                {
                    "error": (
                        f"This order's payment is '{existing_payment.status}' — its "
                        "status can no longer be updated. Use the "
                        "reinstate action if it needs to be reopened."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_status = serializer.validated_data["status"]
        old_status = order.status

        # FIX (Admin dashboard bug report, Sep 2026): once a customer has
        # actually paid (payment.status == "paid"), the order must never
        # be moved back to "pending_payment" — doing so left the order
        # and its payment record out of sync (order list showed
        # "pending_payment" while the order detail / payment still showed
        # "paid"). Block that transition outright, regardless of the
        # order's current status.
        if new_status == "pending_payment":
            payment = getattr(order, "payment", None)
            if payment and payment.status == "paid":
                return Response(
                    {
                        "error": (
                            "This order has already been paid for — its "
                            "status cannot be reverted to 'Pending "
                            "Payment'."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # FIX (Order status workflow bug report, Sep 2026): the status
        # sequence is strictly forward-only — pending_payment -> confirmed
        # -> shipped -> out_for_delivery -> delivered. An admin could
        # previously jump status backward at any point (e.g. shipped back
        # to confirmed, delivered back to shipped) via this endpoint.
        # Block any backward move within that sequence. "cancelled" is
        # handled separately below/above and isn't part of this sequence.
        FORWARD_STATUS_SEQUENCE = [
            "pending_payment", "confirmed", "shipped",
            "out_for_delivery", "delivered",
        ]
        if old_status in FORWARD_STATUS_SEQUENCE and new_status in FORWARD_STATUS_SEQUENCE:
            if FORWARD_STATUS_SEQUENCE.index(new_status) < FORWARD_STATUS_SEQUENCE.index(old_status):
                return Response(
                    {
                        "error": (
                            f"Order status cannot move backward from "
                            f"'{old_status}' to '{new_status}'."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if order.status == "delivered" and new_status == "cancelled":
            return Response(
                {"error": "Delivered orders cannot be cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # FIX (B29): admin could previously mark an order "shipped" or
        # "delivered" even though it had never actually been paid for.
        # Block that transition outright — payment must be confirmed
        # ("paid") before an order can move to confirmed / shipped /
        # out_for_delivery / delivered.
        # FIX (QR proof workflow bug report, Sep 2026): "confirmed" is now
        # included here too — an admin was able to manually confirm an
        # order before its QR payment proof had actually been approved
        # (payment.status still "pending" or "under_review"). Confirming
        # an order should only ever happen once payment.status == "paid"
        # — normally that transition should be automatic, done by the QR
        # proof approval endpoint itself, not typed in manually here.
        if new_status in ("confirmed", "shipped", "out_for_delivery", "delivered"):
            payment = getattr(order, "payment", None)
            if not payment or payment.status != "paid":
                # UPDATED (Supervisor Scenario 11, Sep 2026): wording
                # aligned to spec — "Please approve payment first."
                return Response(
                    {
                        "error": (
                            "Please approve payment first. This order's "
                            "payment has not been confirmed yet — it "
                            "cannot be marked as "
                            f"'{new_status.replace('_', ' ')}' until "
                            "payment is received."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        suggested_alternatives = []
        was_paid_before_cancel = False
        order_is_being_cancelled = False

        if new_status == "cancelled" and order.status != "cancelled":
            order_is_being_cancelled = True
            # FIX (B27): reason is now mandatory for admin cancellations
            # (enforced in AdminOrderStatusSerializer.validate) and gets
            # stored on the order so there's a permanent record of why.
            order.cancellation_reason = serializer.validated_data.get(
                "cancellation_reason", ""
            ).strip()

            # FIX (B27): surface in-stock alternatives for any item that
            # was out of stock, so the admin can pass them on to the
            # customer instead of just saying "sorry, cancelled".
            suggested_alternatives = suggest_alternatives_for_order(order)

            with transaction.atomic():
                # FIX (Cross-check, Sep 2026): same fix as the customer-
                # facing cancel view — release_reserved_stock_for_order()
                # is the correct helper here, not the dead old-schema
                # restore_stock_for_order() (product.stock no longer
                # exists on this schema).
                release_reserved_stock_for_order(order)

                if hasattr(order, "payment"):
                    was_paid_before_cancel = order.payment.status == "paid"
                    order.payment.status = "refunded"
                    order.payment.refunded_at = timezone.now()
                    order.payment.save()

        order.status = new_status

        if (
            "tracking_number" in serializer.validated_data
            and serializer.validated_data["tracking_number"]
        ):
            order.tracking_number = serializer.validated_data[
                "tracking_number"
            ]

        order.save()

        status_titles = {
    "pending_payment": "Awaiting Payment",
    "confirmed": "Order confirmed",
    "shipped": "Order shipped",
    "out_for_delivery": "Out for delivery",
    "delivered": "Order delivered",
    "cancelled": "Order cancelled",
}

        status_messages = {
    "pending_payment": f"Your order {order.order_number} is awaiting payment.",
    "confirmed": f"Order {order.order_number} has been confirmed.",
    # FIX (Order shipped notification bug report, Sep 2026 — Scenario 9):
    # this used to say only "has been shipped", with no tracking number
    # even when the admin provided one in the same request — the
    # customer had no way to actually track their package from this
    # notification. Now includes it whenever one is set on the order.
    "shipped": (
        f"Order {order.order_number} has been shipped. "
        f"Tracking number: {order.tracking_number}."
        if order.tracking_number
        else f"Order {order.order_number} has been shipped."
    ),
    "out_for_delivery": f"Order {order.order_number} is out for delivery.",
    "delivered": f"Order {order.order_number} has been delivered.",
    "cancelled": (
        f"Order {order.order_number} has been cancelled. "
        f"Reason: {order.cancellation_reason}."
        if order.cancellation_reason
        else f"Order {order.order_number} has been cancelled."
    ),
}


        # FIX (B28): this notification already existed and already fires
        # correctly on every admin status change (including cancellation)
        # — kept as-is, just extended with the new statuses above so the
        # customer's status always matches what the admin set.
        create_notification(
            user=order.customer.user,
            store=order.store,
            title=status_titles[new_status],
            message=status_messages[new_status],
            notification_type="order",
            reference_type="order",
            reference_id=order.order_number,
        )

        if new_status == "cancelled" and was_paid_before_cancel:
            send_refund_confirmation_email(order)

        # FIX (Frontend Bug Report — Audit Logs, Sep 2026): no admin write
        # endpoint besides Adjust Stock was writing to the shared AuditLog
        # table (API 82 / System Activity Logs). Logged here now.
        log_admin_action(
            store=order.store,
            user=request.user,
            action="update_order_status",
            entity="order",
            entity_id=order.id,
            old_data={"status": old_status},
            new_data={"status": order.status, "tracking_number": order.tracking_number},
            request=request,
        )

        response_data = OrderDetailSerializer(order).data

        # FIX (Ask 1, Frontend Clarification — Suggested Alternatives):
        # always attach the key whenever this request cancelled the order,
        # even if there was nothing to suggest, so the frontend never has
        # to special-case a missing field — just an empty list.
        if order_is_being_cancelled:
            response_data["suggested_alternatives"] = suggested_alternatives

        return Response(response_data)


# NEW (B29): "ability to re-pay for a reinstated order" — lets an admin
# undo a cancellation and put the order back into pending_payment so the
# customer can pay for it again via the existing, already-working Pay Now
# flow (CreatePaymentIntentView in payments/views.py already supports any
# order_number whose payment isn't "paid" yet — B31 needed no backend
# change, this is what makes it usable for a previously-cancelled order).
class AdminOrderReinstateView(APIView):
    """PUT /api/v1/admin/orders/{order_number}/reinstate/"""
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def put(self, request, order_number):
        try:
            order = Order.objects.get(order_number=order_number)
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if order.status != "cancelled":
            return Response(
                {"error": "Only a cancelled order can be reinstated."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order.status = "pending_payment"
        order.cancellation_reason = None
        order.save()

        if hasattr(order, "payment"):
            # A fresh PaymentIntent must be created next time the customer
            # pays — clearing the old id prevents CreatePaymentIntentView's
            # "already paid" check from getting confused by stale data, and
            # avoids ever reusing a Stripe intent tied to the old attempt.
            order.payment.status = "pending"
            order.payment.stripe_payment_intent_id = None
            order.payment.paid_at = None
            order.payment.refunded_at = None
            order.payment.save()

        create_notification(
            user=order.customer.user,
            store=order.store,
            title="Order Reinstated",
            message=(
                f"Your order #{order.order_number} has been reinstated. "
                "You can complete payment to proceed with it."
            ),
            notification_type="order",
        )

        return Response(OrderDetailSerializer(order).data)

# Returns filtered order list for administrators.
class AdminOrderFilterView(generics.ListAPIView):
    """
    GET /api/v1/admin/orders/filter/

    Query Params:
    - status
    - start_date
    - end_date
    - search
    - customer_id
    - product   (NEW — filters orders containing a product whose name
                 matches, case-insensitive partial match)
    - category  (NEW — filters orders containing a product whose
                 category name matches, case-insensitive partial match)
    - ordering  (NEW — created_at / -created_at / total_amount /
                 -total_amount. Used by the admin Customer Detail
                 Drawer's orders tab so that one customer's full order
                 history can be sorted by Newest/Oldest/Amount server
                 -side instead of only re-sorting the loaded page.
                 Defaults to -created_at when missing/invalid.)
    - page
    """

    serializer_class = AdminOrderListSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    pagination_class = StandardResultsPagination

    # Fixed whitelist so no arbitrary/unsafe column name can be passed in.
    ORDERING_MAP = {
        "created_at": "created_at",
        "-created_at": "-created_at",
        "total_amount": "total_amount",
        "-total_amount": "-total_amount",
    }

# Filters orders using status, customer, dates and search keywords.
    def get_queryset(self):
        qs = (
            Order.objects
            .select_related("customer")
            .all()
            .order_by("-created_at")
        )

        params = self.request.query_params

        # Status filter
        status = params.get("status")
        if status:
            # FIX (Frontend clarification, Sep 2026): "pending" isn't a
            # real Order.status value in this schema — the only
            # pending-type order status is "pending_payment" ("pending"
            # is a Payment status instead, which is likely where the mix
            # -up comes from). Accept "pending" as a shorthand alias so a
            # stray "pending" query still returns the right orders
            # instead of silently coming back empty.
            if status == "pending":
                status = "pending_payment"
            qs = qs.filter(status=status)

        # Customer filter (NEW)
        customer_id = params.get("customer_id")
        if customer_id:
            qs = qs.filter(customer_id=customer_id)

        # Date filters
        start_date = params.get("start_date")
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)

        end_date = params.get("end_date")
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)

        # Search
        search = params.get("search")
        if search:
            qs = qs.filter(
                Q(order_number__icontains=search) |
                Q(customer__name__icontains=search)
            )

        # Product filter (NEW) — admin searches by product name, and we
        # return every order that contains a matching product. Matches
        # against the live product name (items__product__name) and also
        # the snapshot name stored on the order item itself
        # (items__product_name), so orders still match even if the
        # product was later deleted (product FK is SET_NULL on delete).
        product = params.get("product")
        if product:
            qs = qs.filter(
                Q(items__product__name__icontains=product) |
                Q(items__product_name__icontains=product)
            )

        # Category filter (NEW) — admin searches by category name, and we
        # return every order that contains a product from that category.
        category = params.get("category")
        if category:
            qs = qs.filter(
                items__product__category__name__icontains=category
            )

        # Joining through items for the two filters above can duplicate
        # an order row (once per matching item), so de-duplicate here —
        # but only when those filters were actually used, to avoid an
        # unnecessary DISTINCT on the common, unfiltered case.
        if product or category:
            qs = qs.distinct()

        # Ordering (NEW) — only overrides the default -created_at when a
        # whitelisted value is actually passed in.
        ordering = params.get("ordering")
        if ordering in self.ORDERING_MAP:
            qs = qs.order_by(self.ORDERING_MAP[ordering])

        return qs

# Returns complete details of any order for administrators.
class AdminOrderDetailView(generics.RetrieveAPIView):
    serializer_class = OrderDetailSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = "order_number"

    queryset = (
        Order.objects
        .select_related("customer", "store", "payment")
        .prefetch_related("items")
    )