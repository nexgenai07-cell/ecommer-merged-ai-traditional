# PATH: apps/products/services.py

from django.db import transaction
from django.db.models import F, Sum

from .models import Product, ProductVariant, StockMovement
from apps.notifications.utils import create_notification
from .signals import broadcast_product   # NEW (Sep 2026): live stock update


# NEW (Notification Triggers Addendum, Item 17): "Low stock alert".
# Fires the moment a product's available_stock (total_stock -
# reserved_stock) crosses at/below its own low_stock_threshold — only on
# the crossing (old value above the threshold, new value at/below it),
# never repeatedly while stock stays low. Called from both places
# available_stock can actually move: reserve_stock_for_order() in
# apps/orders/views.py (checkout reserves stock, which is where
# available_stock genuinely drops) and adjust_stock() below (manual
# stock adjustment, API 33).
#
# UPDATED (v4.0): store.owner removed — a store now has multiple, equal
# admins (store.admins M2M) instead of one owner. Every admin of the
# store gets notified, never the customer, never a broadcast to all
# users.
def check_low_stock_notification(product, old_available, new_available):
    threshold = product.low_stock_threshold

    if old_available > threshold and new_available <= threshold:
        for admin in product.store.admins.all():
            create_notification(
                user=admin,
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

        # NEW (Sep 2026 — live updates): the .update(F(...)) above bypasses
        # Django signals, so the post_save receiver never fires for it.
        # Broadcast manually (sent only after this transaction commits).
        broadcast_product(product)

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


# ============================================================
# NEW (Oct 2026 — product variants)
# ============================================================

# A product that has variants does not have a stock number of its own
# anymore: Product.total_stock / reserved_stock are the SUM of its
# ACTIVE, non-deleted variants' stock (e.g. 4 variants x 2 units = 8;
# sell the first 2 variants -> 4 left, the variants still show 2 / 2).
# This function recomputes those two numbers from the variants and
# writes them to the product row. Call it after ANYTHING changes a
# variant's total_stock / reserved_stock / is_active / is_delete.
#
# Uses .update() on purpose (like adjust_stock above), so it does not
# re-trigger the Product post_save signal — the live broadcast and the
# low-stock check are done here instead.
#
# log=True also writes a product-level StockMovement when the product's
# total_stock actually changes — used for admin actions that change the
# product total without a variant-level movement of their own (first
# variant replacing the old hand-typed stock, deleting a variant).
def sync_product_stock_from_variants(
    product,
    *,
    changed_by=None,
    log=False,
    reason="correction",
    note="",
):
    with transaction.atomic():
        locked = Product.objects.select_for_update().get(pk=product.pk)

        totals = ProductVariant.objects.filter(
            product=locked,
            is_delete=False,
            is_active=True,
        ).aggregate(total=Sum("total_stock"), reserved=Sum("reserved_stock"))

        new_total = totals["total"] or 0
        new_reserved = totals["reserved"] or 0
        old_total = locked.total_stock
        old_reserved = locked.reserved_stock

        if (old_total, old_reserved) == (new_total, new_reserved):
            return locked

        Product.objects.filter(pk=locked.pk).update(
            total_stock=new_total,
            reserved_stock=new_reserved,
        )
        locked.refresh_from_db()

        if log and new_total != old_total:
            StockMovement.objects.create(
                product=locked,
                changed_by=changed_by,
                old_stock=old_total,
                new_stock=new_total,
                delta=new_total - old_total,
                reason=reason,
                note=note,
            )

        broadcast_product(locked)

        check_low_stock_notification(
            locked,
            old_available=old_total - old_reserved,
            new_available=new_total - new_reserved,
        )

        return locked


# Same idea as adjust_stock() above, but for ONE variant: atomically
# changes that variant's total_stock (never reserved_stock), records a
# StockMovement tied to the variant, then re-syncs the product total.
def adjust_variant_stock(
    *,
    variant,
    delta,
    reason,
    changed_by=None,
    note="",
):
    if delta == 0:
        raise ValueError("delta cannot be 0.")

    with transaction.atomic():
        locked = (
            ProductVariant.objects
            .select_for_update()
            .select_related("product")
            .get(pk=variant.pk)
        )

        previous_stock = locked.total_stock
        new_stock = previous_stock + delta

        if new_stock < 0:
            raise ValueError(
                f"Cannot reduce stock below 0. "
                f"Current stock: {previous_stock}, "
                f"requested change: {delta}"
            )

        if new_stock < locked.reserved_stock:
            raise ValueError(
                f"Cannot reduce stock below the reserved amount "
                f"({locked.reserved_stock} reserved for pending orders). "
                f"Current stock: {previous_stock}, "
                f"requested change: {delta}"
            )

        ProductVariant.objects.filter(pk=locked.pk).update(
            total_stock=F("total_stock") + delta
        )
        locked.refresh_from_db()

        StockMovement.objects.create(
            product=locked.product,
            variant=locked,
            changed_by=changed_by,
            old_stock=previous_stock,
            new_stock=locked.total_stock,
            delta=delta,
            reason=reason,
            note=note,
        )

        product = sync_product_stock_from_variants(locked.product)

        return {
            "variant_id": locked.id,
            "total_stock": locked.total_stock,
            "reserved_stock": locked.reserved_stock,
            "available_stock": locked.total_stock - locked.reserved_stock,
            "previous_stock": previous_stock,
            "delta_applied": delta,
            "product": {
                "id": product.id,
                "total_stock": product.total_stock,
                "reserved_stock": product.reserved_stock,
                "available_stock": product.total_stock - product.reserved_stock,
            },
        }