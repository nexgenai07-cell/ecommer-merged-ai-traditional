# PATH: apps/orders/apps.py

import os
import sys

from django.apps import AppConfig


class OrdersConfig(AppConfig):
    name = 'apps.orders'

    def ready(self):
        # NEW (Sep 2026 — auto-cancel scheduling fix): starts the
        # in-process scheduler (apps/orders/scheduler.py) that runs the
        # cancel_stale_payments management command every 60 seconds —
        # see that module's docstring for why (no cron/Celery Beat
        # access on this VPS deployment).
        #
        # Set ENABLE_SCHEDULER=False in the environment to turn this off
        # entirely (e.g. if a real cron/Celery Beat gets set up later
        # and you don't want both running).
        if os.environ.get("ENABLE_SCHEDULER", "True") != "True":
            return

        argv = sys.argv
        invoked_via_manage_py = bool(argv) and os.path.basename(argv[0]) == "manage.py"

        if invoked_via_manage_py:
            # One-off management commands (migrate, shell, makemigrations,
            # createsuperuser, cancel_stale_payments itself, etc.) should
            # NOT spin up a background scheduler thread — only the actual
            # dev server should.
            command = argv[1] if len(argv) > 1 else None
            if command != "runserver":
                return

            # `runserver` re-executes itself once via Django's
            # auto-reloader, so without this check the scheduler would
            # start twice (once in the reloader's parent "watcher"
            # process, again in the real child process). RUN_MAIN is
            # only set in that child process. If the reloader is
            # disabled (`runserver --noreload`), RUN_MAIN is never set
            # at all, so we still start in that case.
            autoreload_disabled = "--noreload" in argv
            if not autoreload_disabled and os.environ.get("RUN_MAIN") != "true":
                return

        # Production (gunicorn/daphne invoked directly, not via
        # manage.py) lands here unconditionally and always starts the
        # scheduler — this is the actual VPS deployment path.
        from . import scheduler
        scheduler.start()