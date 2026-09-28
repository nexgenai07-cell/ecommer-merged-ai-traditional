from django.apps import AppConfig


class CartConfig(AppConfig):
    name = 'apps.cart'

    def ready(self):
        # NEW (Sep 2026 — live updates): registers the post_save/post_delete receivers.
        from . import signals  # noqa: F401
