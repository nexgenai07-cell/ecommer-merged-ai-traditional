# PATH: apps/notifications/management/commands/cleanup_old_data.py
#
# Cron job: weekly housekeeping.
#   - clears expired Django sessions (built-in `clearsessions`)
#   - deletes READ notifications older than 90 days
#   - deletes guest carts (no user) idle for 30+ days
# Nothing here touches a customer's own account, orders, or unread
# notifications.
#
# Manual test:  python manage.py cleanup_old_data

import logging
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)

NOTIFICATION_RETENTION_DAYS = 90
GUEST_CART_RETENTION_DAYS = 30


class Command(BaseCommand):
    help = "Weekly housekeeping: expired sessions, old read notifications, abandoned guest carts."

    def handle(self, *args, **options):
        from apps.notifications.models import Notification
        from apps.cart.models import Cart

        call_command("clearsessions")

        notif_cutoff = timezone.now() - timedelta(days=NOTIFICATION_RETENTION_DAYS)
        deleted_notifs, _ = Notification.objects.filter(
            is_read=True, created_at__lt=notif_cutoff,
        ).delete()

        cart_cutoff = timezone.now() - timedelta(days=GUEST_CART_RETENTION_DAYS)
        deleted_carts, _ = Cart.objects.filter(
            user__isnull=True, updated_at__lt=cart_cutoff,
        ).delete()

        self.stdout.write(self.style.SUCCESS(
            f"Cleanup done — sessions cleared, {deleted_notifs} old notification(s) "
            f"and {deleted_carts} abandoned guest cart(s) removed."
        ))
