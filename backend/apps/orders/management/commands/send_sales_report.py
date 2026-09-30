# PATH: apps/orders/management/commands/send_sales_report.py
#
# Cron job: emails + notifies each store's admins a summary of
# yesterday's orders/revenue. On Mondays, also appends the last 7 days.
# Uses Order.REVENUE_STATUSES — the same "what counts as revenue" rule
# used by the admin dashboard (confirmed/shipped/out_for_delivery/
# delivered), so this report never disagrees with the dashboard.
#
# Manual test:  python manage.py send_sales_report

import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db.models import Count, Sum
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.notifications.email_templates import sales_report_html

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Emails/notifies each store's admins a daily (and, on Mondays, weekly) sales summary."

    def handle(self, *args, **options):
        from apps.orders.models import Order
        from apps.stores.models import Store
        from apps.notifications.utils import notify_store_admins

        now = timezone.localtime(timezone.now())
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_start = today_start - timedelta(days=1)
        is_monday = now.weekday() == 0  # Monday

        def period_stats(store, start, end):
            agg = Order.objects.filter(
                store=store, created_at__gte=start, created_at__lt=end,
                status__in=Order.REVENUE_STATUSES,
            ).aggregate(revenue=Sum("total_amount"), orders=Count("id"))
            return agg["orders"] or 0, agg["revenue"] or 0

        sent = 0
        for store in Store.objects.all():
            day_orders, day_revenue = period_stats(store, yesterday_start, today_start)

            body_lines = [f"Yesterday ({yesterday_start:%d %b %Y}): {day_orders} orders, Rs. {day_revenue} revenue."]
            if is_monday:
                week_orders, week_revenue = period_stats(store, today_start - timedelta(days=7), today_start)
                body_lines.append(f"Last 7 days: {week_orders} orders, Rs. {week_revenue} revenue.")
            body = "\n".join(body_lines)
            sections = [(f"Yesterday ({yesterday_start:%d %b %Y})", day_orders, day_revenue)]
            if is_monday:
                sections.append(("Last 7 days", week_orders, week_revenue))

            try:
                notify_store_admins(
                    store,
                    title="Daily sales report",
                    message=body,
                    notification_type="system",
                )
                self._email_admins(store, f"Sales report — {store.name} — {yesterday_start:%d %b %Y}", body, sections)
                sent += 1
            except Exception:
                logger.exception("sales report failed for store %s", store.id)

        self.stdout.write(self.style.SUCCESS(f"Sales report sent for {sent} store(s)."))

    def _email_admins(self, store, subject, body, sections=None):
        emails = [u.email for u in store.admins.all() if u.email]
        if not emails:
            return
        html_message = sales_report_html(store.name, sections or [], body)
        try:
            msg = EmailMultiAlternatives(
                subject=subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL, to=emails,
            )
            msg.attach_alternative(html_message, "text/html")
            msg.send(fail_silently=True)
        except Exception:
            logger.exception("sales report email failed for store %s", store.id)