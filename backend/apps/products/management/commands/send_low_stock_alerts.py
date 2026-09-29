# PATH: apps/products/management/commands/send_low_stock_alerts.py
#
# Cron job: notifies each store's admins about products at or below
# their own low_stock_threshold (same rule as the Low Stock Products /
# Inventory Alerts endpoint, API 38). Dedup via Redis so the same
# product isn't re-notified on every tick — only once every 20 hours,
# even if it's still low next time this job runs.
#
# Manual test:  python manage.py send_low_stock_alerts

import logging
from collections import defaultdict

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db.models import F

logger = logging.getLogger(__name__)

DEDUPE_SECONDS = 20 * 3600


class Command(BaseCommand):
    help = "Notifies store admins about products at or below their low-stock threshold."

    def handle(self, *args, **options):
        from apps.products.models import Product
        from apps.notifications.utils import notify_store_admins

        candidates = (
            Product.objects.filter(is_active=True, is_delete=False)
            .annotate(_available=F("total_stock") - F("reserved_stock"))
            .filter(_available__lte=F("low_stock_threshold"))
            .select_related("store")
        )

        by_store = defaultdict(list)
        for product in candidates:
            dedupe_key = f"low_stock_notified:{product.id}"
            if not cache.add(dedupe_key, "1", timeout=DEDUPE_SECONDS):
                continue
            by_store[product.store].append(product)

        notified = 0
        for store, products in by_store.items():
            names = ", ".join(
                f"{p.name} ({max(p.total_stock - p.reserved_stock, 0)} left)"
                for p in products[:5]
            )
            more = f" and {len(products) - 5} more" if len(products) > 5 else ""
            try:
                notify_store_admins(
                    store,
                    title=f"{len(products)} product(s) running low on stock",
                    message=f"{names}{more}.",
                    notification_type="system",
                    reference_type="product",
                    reference_id=products[0].id,
                )
                notified += len(products)
            except Exception:
                logger.exception("low stock notify failed for store %s", store.id)

        self.stdout.write(self.style.SUCCESS(f"Low-stock alerts sent for {notified} product(s)."))
