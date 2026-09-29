# PATH: apps/products/management/commands/notify_expired_discounts.py
#
# Cron job: tells store admins when a coupon has just expired.
#
# IMPORTANT: this does NOT flip Discount.is_active to False. The status
# shown by List Discounts (API 39, v5.7) is computed at read-time from
# end_date, keeping "expired" (time-based) and "inactive" (manually
# turned off by an admin) as two distinct, separate values. Auto-setting
# is_active here would collapse that distinction and reintroduce the
# exact bug v5.7 fixed. This command only sends a heads-up notification.
#
# Manual test:  python manage.py notify_expired_discounts

import logging
from datetime import timedelta

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)

DEDUPE_SECONDS = 48 * 3600


class Command(BaseCommand):
    help = "Notifies store admins about coupons that expired in the last 24 hours."

    def handle(self, *args, **options):
        from apps.products.models import Discount
        from apps.notifications.utils import notify_store_admins

        now = timezone.now()
        window_start = now - timedelta(hours=24)
        expired = Discount.objects.filter(
            is_delete=False,
            is_active=True,
            end_date__gte=window_start,
            end_date__lt=now,
        ).select_related("store")

        notified = 0
        for discount in expired:
            dedupe_key = f"discount_expiry_notified:{discount.id}"
            if not cache.add(dedupe_key, "1", timeout=DEDUPE_SECONDS):
                continue
            try:
                notify_store_admins(
                    discount.store,
                    title="Coupon expired",
                    message=f'Coupon "{discount.code}" expired on '
                            f'{timezone.localtime(discount.end_date):%d %b %Y, %I:%M %p}.',
                    notification_type="system",
                )
                notified += 1
            except Exception:
                logger.exception("expired-discount notify failed for discount %s", discount.id)

        self.stdout.write(self.style.SUCCESS(f"Expired-coupon notifications sent: {notified}"))
