# PATH: apps/orders/management/commands/backfill_customers.py
#
# ONE-TIME FIX for the "admin Customers page missing users" bug: run this
# once, right after deploying the create_customer_profile_for_new_user
# signal (apps/users/signals.py), so every account that registered BEFORE
# the signal existed also gets a Customer row immediately, instead of
# only becoming visible the next time each of them happens to log in.
#
# Usage:
#   python manage.py backfill_customers
#
# Safe to run more than once — get_or_create_customer() is idempotent
# (it uses Customer.objects.get_or_create under the hood), so users who
# already have a Customer row (from a past order, or the new signal) are
# simply skipped.

from django.core.management.base import BaseCommand

from apps.orders.views import get_or_create_customer
from apps.stores.models import Store
from apps.users.models import User


class Command(BaseCommand):
    help = "Creates a Customer row for every existing 'customer'-role user who doesn't already have one."

    def handle(self, *args, **options):
        store = Store.objects.first()
        if store is None:
            self.stderr.write(self.style.ERROR(
                "No Store exists yet — create a Store first, then re-run this command."
            ))
            return

        users = User.objects.filter(role="customer")
        processed_count = 0
        failed_users = []

        for user in users:
            # One bad row (e.g. an unexpected DB error) must not abort
            # the whole backfill and leave every user after it
            # unprocessed — catch per-user and keep going.
            try:
                get_or_create_customer(user, store_id=store.id)
                processed_count += 1
            except Exception as exc:
                failed_users.append((user, exc))

        self.stdout.write(self.style.SUCCESS(
            f"Processed {processed_count} customer-role user(s) against store "
            f"'{store}'. Any who didn't already have a Customer row for this "
            f"store now do."
        ))

        if failed_users:
            self.stdout.write(self.style.WARNING(
                f"{len(failed_users)} user(s) could not be processed:"
            ))
            for user, exc in failed_users:
                self.stdout.write(f"  - {user.email} (id={user.id}): {exc}")