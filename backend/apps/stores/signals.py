from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

# Abhi platform ek hi store ka hai (supervisor ke mutabiq id=1).
# Multi-store aaye to is constant ki jagah proper logic lagega.
DEFAULT_STORE_ID = 1


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def link_admin_to_default_store(sender, instance, **kwargs):
    if getattr(instance, "role", None) != "admin":
        return

    from apps.stores.models import Store

    store = Store.objects.filter(pk=DEFAULT_STORE_ID).first()
    if store is None:
        return

    # add() duplicate nahi banata, isliye dobara chalane se koi masla nahi
    store.admins.add(instance)