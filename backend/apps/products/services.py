from django.db import transaction
from django.db.models import F

from .models import Product, StockMovement
from apps.notifications.utils import create_notification


# NEW (Notification Triggers Addendum, Item 17): "Low stock alert".
# Fires the moment a product's available_stock (total_stock -
# reserved_stock) crosses at/below its own low_stock_threshold — only on
# the crossing (old value above the threshold, new value at/below it),
# never repeatedly while stock stays low. Called from both places
# available_stock can actually move: reserve_stock_for_order() in
# apps/orders/views.py (checkout reserves stock, which is where
# available_stock genuinely drops) and adjust_stock() below (manual
# stock adjustment, API 33). Notified user is always the owning store's
# admin/owner (store.owner) — never the customer, never a broadcast.
def check_low_stock_notification(product, old_available, new_available):
    threshold = product.low_stock_threshold

    if old_available > threshold and new_available <= threshold:
        create_notification(
            user=product.store.owner,
            store=product.store,
            title="Low stock alert",
            message=(
                f"{product.name} is running low on stock "
                f"({new_available} left, threshold: {threshold})."
            ),
            notification_type="system",
            reference_type="product",
            reference_id=product.id,
        )


# Safely updates product stock and records every stock movement.
def adjust_stock(
    *,
    product,
    delta,
    reason,
    changed_by=None,
    note=""
):
    """
    Atomically adjust product stock and record a StockMovement.

    ============================================================
    PDF Part 2 Item 5: Adjust Stock (API 33) delta operates on
    total_stock ONLY. It never touches reserved_stock.
    ============================================================
    """

    if delta == 0:
        raise ValueError("delta cannot be 0.")

# Executes all database operations as a single transaction.
    with transaction.atomic():

        # Lock this product row until transaction completes
        product = (  
            Product.objects
            .select_for_update()  # Locks the product row to prevent simultaneous stock updates.
            .get(pk=product.pk)
        )
     
        # ============================================================
        # NEW: Use total_stock instead of stock
        # ============================================================
        previous_stock = product.total_stock

        if previous_stock + delta < 0:  # Prevents stock from becoming negative.
            raise ValueError(
                f"Cannot reduce stock below 0. "
                f"Current stock: {previous_stock}, "
                f"requested change: {delta}"
            )

        # ============================================================
        # NEW: Update total_stock only - reserved_stock is never touched
        # ============================================================
        Product.objects.filter(
            pk=product.pk
        ).update(
            total_stock=F("total_stock") + delta
        )

        product.refresh_from_db() # Reloads the updated product from the database.

        # ============================================================
        # NEW: Log with total_stock values
        # ============================================================
        StockMovement.objects.create(
            product=product,
            changed_by=changed_by,
            old_stock=previous_stock,
            new_stock=product.total_stock,
            delta=delta,
            reason=reason,
            note=note,
        )

        # NEW (Notification Triggers Addendum, Item 17): reserved_stock is
        # never touched by this endpoint, so available_stock moves 1:1
        # with total_stock here — check for a threshold crossing after
        # the update.
        check_low_stock_notification(
            product,
            old_available=previous_stock - product.reserved_stock,
            new_available=product.total_stock - product.reserved_stock,
        )

        # ============================================================
        # NEW: Return updated stock information with all three fields
        # ============================================================
        return {
            "id": product.id,
            "total_stock": product.total_stock,
            "reserved_stock": product.reserved_stock,
            "available_stock": product.total_stock - product.reserved_stock,
            "previous_stock": previous_stock,
            "delta_applied": delta,
        }