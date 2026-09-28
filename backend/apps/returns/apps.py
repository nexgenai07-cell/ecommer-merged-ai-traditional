from django.apps import AppConfig


class ReturnsConfig(AppConfig):
    name = 'apps.returns'

    def ready(self):
        # NEW (Sep 2026 — live updates): registers the post_save/post_delete receivers.
        from . import signals  # noqa: F401
