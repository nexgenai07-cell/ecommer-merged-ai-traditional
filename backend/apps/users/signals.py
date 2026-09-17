# PATH: apps/users/signals.py

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import User


# FIX (Admin Dashboard — Customers page missing users): a Customer row
# (the thing AdminCustomerListView in apps/orders/customer_views.py
# lists) used to only get created the first time a user placed an order
# or saved a checkout address, via get_or_create_customer() in
# apps/orders/views.py. Anyone who registered and logged in but never
# checked out was completely invisible on the admin Customers page.
#
# This signal fires the moment a new User row is actually inserted
# (created=True), for EVERY signup path (normal register, Google login,
# etc., since they all end up saving a User) — so a Customer profile now
# exists as soon as the account does, matching the intended behaviour:
# "user signs up / logs in -> counts as a customer".
#
# NOTE: this only covers users created from now on. Existing accounts
# that registered before this fix still need a one-time backfill — see
# apps/orders/management/commands/backfill_customers.py.
@receiver(post_save, sender=User)
def create_customer_profile_for_new_user(sender, instance, created, **kwargs):
    if not created:
        return

    # Admins/moderators don't have storefront Customer profiles — only
    # 'customer'-role users do (see Customer being store-scoped, per the
    # comment on User.profile_picture in models.py).
    if instance.role != "customer":
        return

    # Imported here, not at module level: apps.orders.views imports from
    # apps.users.permissions, apps.notifications, apps.cart, apps.products
    # etc. at module load time, so importing it back at the top of this
    # file (which apps.users/apps.py loads during app startup) risks a
    # circular/partial-app-registry import. Deferring the import until the
    # signal actually fires (well after all apps are ready) avoids that.
    from apps.orders.views import get_or_create_customer
    from apps.stores.models import Store

    store = Store.objects.first()
    if store is None:
        # No store configured yet — same "log and skip" philosophy as
        # notifications/utils.py create_notification(); the Customer row
        # will simply get created later (e.g. next login) once a store
        # exists, or via the backfill command.
        return

    get_or_create_customer(instance, store_id=store.id)