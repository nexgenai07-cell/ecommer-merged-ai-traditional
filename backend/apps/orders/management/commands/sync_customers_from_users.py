# PATH: apps/orders/management/commands/sync_customers_from_users.py
#
# ONE-TIME FIX for accounts whose profile was edited BEFORE
# apps/users/customer_sync.py existed: their Customer row (read by the
# admin Customers page, order pages, e-mails, ...) still holds the old
# name / email / phone. This copies the current account values onto
# every Customer row that has an account (guest customers, which have no
# account, are never touched).
#
# Usage:
#   python manage.py sync_customers_from_users
#
# Safe to run more than once. A phone that is already used by another
# customer in the same store is skipped (unique constraint) and reported.

from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction

from apps.orders.models import Customer


class Command(BaseCommand):
    help = "Copies current User name/email/phone onto their Customer rows."

    def handle(self, *args, **options):
        updated = 0
        phone_conflicts = []

        for customer in Customer.objects.select_related("user").filter(
            user__isnull=False
        ):
            user = customer.user
            changes = {}
            if customer.name != user.name:
                changes["name"] = user.name
            if customer.email != user.email:
                changes["email"] = user.email
            if changes:
                Customer.objects.filter(pk=customer.pk).update(**changes)
                updated += 1

            new_phone = user.phone or ""
            if new_phone and customer.phone != new_phone:
                try:
                    with transaction.atomic():
                        Customer.objects.filter(pk=customer.pk).update(
                            phone=new_phone
                        )
                    updated += 1
                except IntegrityError:
                    phone_conflicts.append((user.email, new_phone))

        self.stdout.write(self.style.SUCCESS(
            f"Updated {updated} customer field group(s)."
        ))
        for email, phone in phone_conflicts:
            self.stdout.write(self.style.WARNING(
                f"  phone not copied for {email}: {phone} already used by "
                f"another customer in this store."
            ))
