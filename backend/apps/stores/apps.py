from django.apps import AppConfig


class StoresConfig(AppConfig):
    name = 'apps.stores'

    def ready(self):
        from . import signals  # noqa: F401