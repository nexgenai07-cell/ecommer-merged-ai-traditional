# PATH: apps/notifications/cron_scheduler.py
#
# In-process background scheduler (APScheduler) for jobs that run on a
# fixed schedule instead of reacting to a request — same technique as
# apps/orders/scheduler.py (this VPS deployment has no separate
# cron/Celery Beat access), kept as its OWN scheduler/thread so this
# never touches or risks the existing cancel_stale_payments scheduler.
# Started from apps/notifications/apps.py -> NotificationsConfig.ready().
#
# Jobs (all run as management commands, each independently testable —
# see each command's own docstring for how to run it by hand):
#   send_abandoned_cart_reminders   every hour, on the hour
#   send_low_stock_alerts           every 6 hours
#   notify_expired_discounts        daily  00:15  (settings.TIME_ZONE)
#   send_sales_report               daily  08:00  (settings.TIME_ZONE)
#   cleanup_old_data                weekly Sun 03:00 (settings.TIME_ZONE)
#
# Every job goes through _run(): same two safety fixes as the existing
# scheduler (see its docstring) — a background thread never gets
# Django's normal per-request "close dead connections" cleanup, and a
# cache outage must never stop the job from running.

import logging

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.db import close_old_connections, connections

logger = logging.getLogger(__name__)

_scheduler_started = False  # guard against starting twice in one process

# (job_id, management command name, lock_timeout_seconds — slightly under
# this job's own interval, so a lock left behind by a crashed run can't
# block that job forever)
_JOBS = [
    ("send_abandoned_cart_reminders", "send_abandoned_cart_reminders", 55 * 60),
    ("send_low_stock_alerts", "send_low_stock_alerts", 5 * 3600 + 55 * 60),
    ("notify_expired_discounts", "notify_expired_discounts", 23 * 3600),
    ("send_sales_report", "send_sales_report", 23 * 3600),
    ("cleanup_old_data", "cleanup_old_data", 6 * 24 * 3600),
]


def _run(job_id, command_name, lock_timeout):
    """Called by APScheduler. Never raises — a failed run is logged, not fatal."""
    lock_key = f"cron:{job_id}:lock"
    try:
        got_lock = cache.add(lock_key, "1", timeout=lock_timeout)
    except Exception:
        logger.exception("%s: cache lock unavailable, running without lock", job_id)
        got_lock = True

    if not got_lock:
        return  # another worker process already runs this tick

    try:
        close_old_connections()
        call_command(command_name)
    except Exception:
        logger.exception("%s: scheduled run failed", job_id)
    finally:
        connections.close_all()


def start():
    """Starts the background scheduler. Safe to call more than once per
    process — only the first call actually starts anything."""
    global _scheduler_started
    if _scheduler_started:
        return

    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    tz = getattr(settings, "TIME_ZONE", "Asia/Karachi")
    scheduler = BackgroundScheduler(daemon=True, timezone=tz)

    def add(job_id, command_name, lock_timeout, trigger):
        scheduler.add_job(
            _run, trigger, args=[job_id, command_name, lock_timeout],
            id=job_id, replace_existing=True, max_instances=1,
            coalesce=True, misfire_grace_time=15 * 60,
        )

    jobs_by_id = {j[0]: j for j in _JOBS}
    add(*jobs_by_id["send_abandoned_cart_reminders"], trigger=CronTrigger(minute=0))
    add(*jobs_by_id["send_low_stock_alerts"], trigger=CronTrigger(hour="*/6", minute=5))
    add(*jobs_by_id["notify_expired_discounts"], trigger=CronTrigger(hour=0, minute=15))
    add(*jobs_by_id["send_sales_report"], trigger=CronTrigger(hour=8, minute=0))
    add(*jobs_by_id["cleanup_old_data"], trigger=CronTrigger(day_of_week="sun", hour=3, minute=0))

    scheduler.start()
    _scheduler_started = True

    job_ids = ", ".join(j[0] for j in _JOBS)
    logger.info("cron_scheduler: started (%s)", job_ids)
    print(f"[cron_scheduler] started: {job_ids}", flush=True)
