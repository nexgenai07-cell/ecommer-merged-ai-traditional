from django.apps import AppConfig

import os
import sys


class NotificationsConfig(AppConfig):
    name = 'apps.notifications'

    def ready(self):
        # NEW (Sep 2026 -- live updates): registers the post_save receiver.
        from . import signals  # noqa: F401

        # NEW (Sep 2026 -- cron jobs): starts the in-process scheduler
        # (apps/notifications/cron_scheduler.py) for abandoned-cart
        # reminders, low-stock alerts, expired-coupon notices, the daily
        # sales report and weekly cleanup. Same start/guard pattern as
        # apps/orders/apps.py's own scheduler -- see that file's comments
        # for why each check below is needed. Set ENABLE_SCHEDULER=False
        # in the environment to turn every in-process scheduler off.
        if os.environ.get("ENABLE_SCHEDULER", "True") != "True":
            return

        argv = sys.argv
        invoked_via_manage_py = bool(argv) and os.path.basename(argv[0]) == "manage.py"

        if invoked_via_manage_py:
            command = argv[1] if len(argv) > 1 else None
            if command != "runserver":
                return

            autoreload_disabled = "--noreload" in argv
            if not autoreload_disabled and os.environ.get("RUN_MAIN") != "true":
                return

        from . import cron_scheduler
        cron_scheduler.start()
