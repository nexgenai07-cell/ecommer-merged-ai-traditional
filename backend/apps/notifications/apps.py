from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    name = 'apps.notifications'

    def ready(self):
        # NEW (Sep 2026 — live updates): registers the post_save receiver.
        from . import signals  # noqa: F401
