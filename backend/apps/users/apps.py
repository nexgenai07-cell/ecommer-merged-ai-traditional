from django.apps import AppConfig


class UsersConfig(AppConfig):
    name = "apps.users"

    def ready(self):
        # Connects create_customer_profile_for_new_user (signals.py) —
        # without this import, the @receiver decorator in signals.py
        # never runs and the signal silently never fires.
        import apps.users.signals  # noqa: F401