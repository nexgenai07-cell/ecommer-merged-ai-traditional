# PATH: apps/cart/management/commands/send_abandoned_cart_reminders.py
#
# Cron job: notify + email a logged-in customer whose cart has sat
# untouched for 24+ hours, so they come back and finish checking out.
#
# Guest carts (no user, session_key only) are skipped — there is no
# email/account to remind. Dedup uses Redis (cache.add), keyed by the
# cart's own updated_at, so:
#   - the SAME idle cart is reminded only once, ever (until it changes)
#   - if the customer comes back, adds something, then goes idle again,
#     updated_at changes -> a fresh reminder is allowed.
# No new DB column/migration needed.
#
# Manual test:  python manage.py send_abandoned_cart_reminders

import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.notifications.email_templates import abandoned_cart_html

logger = logging.getLogger(__name__)

REMINDER_AFTER_HOURS = 24
DEDUPE_SECONDS = 7 * 24 * 3600  # don't remind about the same cart state twice within a week


class Command(BaseCommand):
    help = "Emails/notifies customers whose cart has been idle for 24+ hours."

    def handle(self, *args, **options):
        from apps.cart.models import Cart
        from apps.orders.models import Customer
        from apps.notifications.utils import create_notification

        cutoff = timezone.now() - timedelta(hours=REMINDER_AFTER_HOURS)
        carts = (
            Cart.objects.filter(user__isnull=False, updated_at__lte=cutoff)
            .select_related("store", "user")
            .prefetch_related("items__product")
        )

        sent = 0
        for cart in carts:
            items = list(cart.items.all())
            if not items:
                continue

            dedupe_key = f"cart_reminder_sent:{cart.id}:{int(cart.updated_at.timestamp())}"
            if not cache.add(dedupe_key, "1", timeout=DEDUPE_SECONDS):
                continue  # already reminded for this exact cart state

            try:
                self._notify(cart, items, create_notification, Customer)
                sent += 1
            except Exception:
                logger.exception("abandoned cart reminder failed for cart %s", cart.id)

        self.stdout.write(self.style.SUCCESS(f"Abandoned cart reminders sent: {sent}"))

    def _notify(self, cart, items, create_notification, Customer):
        item_count = sum(i.quantity for i in items)
        names = ", ".join(i.product.name for i in items[:3])
        more = f" and {len(items) - 3} more" if len(items) > 3 else ""

        create_notification(
            user=cart.user,
            store=cart.store,
            title="You left something in your cart",
            message=f"You have {item_count} item(s) waiting: {names}{more}.",
            notification_type="promotion",
            reference_type="product",
            reference_id=items[0].product_id,
        )

        customer = Customer.objects.filter(user=cart.user, store=cart.store).first()
        email = getattr(customer, "email", None)
        if not email:
            return

        lines = [f"Hi {customer.name},", "", "You still have items waiting in your cart:", ""]
        for i in items:
            lines.append(f"  - {i.quantity} x {i.product.name}")
        lines += ["", "Complete your order before it sells out."]
        message = "\n".join(lines)
        html_message = abandoned_cart_html(
            customer.name,
            [(i.product.name, i.quantity) for i in items],
            message,
        )

        try:
            email_msg = EmailMultiAlternatives(
                subject="You left something in your cart",
                body=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[email],
            )
            email_msg.attach_alternative(html_message, "text/html")
            email_msg.send(fail_silently=True)
        except Exception:
            logger.exception("abandoned cart reminder email failed for cart %s", cart.id)