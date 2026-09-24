# PATH: apps/orders/scheduler.py
#
# In-process background job (APScheduler) that runs the
# `cancel_stale_payments` management command every 60 seconds.
# Started from apps/orders/apps.py -> OrdersConfig.ready().
#
# Requires the `apscheduler` package in requirements.txt.
#
# UPDATED (Sep 2026 — auto-cancel not firing fix):
#   1. DB CONNECTION FIX (main bug): this job runs in a plain background
#      thread, NOT inside a request, so Django never runs its normal
#      "close old/dead connections" cleanup for it. The thread kept ONE
#      Postgres connection open forever; once Neon (or a pooler / the
#      network) dropped that idle connection, every later tick failed
#      with "connection already closed" / "server closed the connection
#      unexpectedly" — the error was only logged, so orders silently
#      stopped being cancelled. Now every tick starts by discarding dead
#      connections and ends by closing its connection, so each tick
#      always opens a fresh, healthy one.
#   2. The Redis lock call used to sit OUTSIDE the try/except — if Redis
#      (Upstash) hiccuped, the tick died before doing anything. Now a
#      cache failure just means "run without the lock" (the command is
#      safe to run concurrently anyway).
#   3. misfire_grace_time / coalesce added so a briefly delayed tick is
#      still executed instead of being skipped.
#   4. A startup line is printed so you can confirm in the server logs
#      that the scheduler really started in that process.
#
# MULTIPLE WORKERS: each worker process starts its own scheduler, but the
# cache lock below makes only ONE of them do the work on a given tick.

import logging

from django.core.cache import cache
from django.core.management import call_command
from django.db import close_old_connections, connections

logger = logging.getLogger(__name__)

_LOCK_KEY = "cancel_stale_payments:scheduler_lock"
# Slightly under the 60-second tick interval, so a lock left behind by a
# worker that crashed mid-run can't block every future tick forever.
_LOCK_TIMEOUT_SECONDS = 55

_scheduler_started = False  # guard against starting twice in one process


def run_cancel_stale_payments():
    """Called by APScheduler every 60 seconds."""
    # 1) Cross-process lock. If the cache itself is broken, don't give
    #    up — just run without the lock (command is concurrency-safe).
    try:
        got_lock = cache.add(_LOCK_KEY, "1", timeout=_LOCK_TIMEOUT_SECONDS)
    except Exception:
        logger.exception(
            "cancel_stale_payments: cache lock unavailable, running "
            "without lock"
        )
        got_lock = True

    if not got_lock:
        # Another worker process already grabbed this tick.
        return

    try:
        # 2) Throw away any connection that died while idle, so this
        #    tick starts on a fresh one.
        close_old_connections()
        call_command("cancel_stale_payments")
    except Exception:
        # Never let a failure kill the background thread — but do make
        # it visible in the server logs.
        logger.exception("cancel_stale_payments: scheduled run failed")
    finally:
        # 3) Never keep a DB connection open between ticks.
        connections.close_all()


def start():
    """
    Starts the background scheduler. Safe to call more than once per
    process — only the first call actually starts anything.
    """
    global _scheduler_started
    if _scheduler_started:
        return

    from datetime import timedelta
    from django.utils import timezone
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        run_cancel_stale_payments,
        "interval",
        seconds=60,
        id="cancel_stale_payments",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
        # First run 10 seconds after startup, then every 60 seconds.
        next_run_time=timezone.now() + timedelta(seconds=10),
    )
    scheduler.start()
    _scheduler_started = True

    logger.info(
        "cancel_stale_payments: in-app scheduler started "
        "(runs every 60 seconds)"
    )
    # Plain print so it shows up in journalctl/gunicorn logs even when
    # INFO-level logging is not configured.
    print(
        "[scheduler] cancel_stale_payments scheduler started "
        "(every 60 seconds)",
        flush=True,
    )