from django.apps import AppConfig


class ProductsConfig(AppConfig):
    name = 'apps.products'

    def ready(self):
        # NEW (Sep 2026): live price/stock updates — registers the post_save receiver.
        from . import signals  # noqa: F401
