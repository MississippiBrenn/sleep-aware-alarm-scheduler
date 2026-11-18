"""The overnight loop. Every few minutes it reads sleep data, re-plans the wake
time, and fires the Alexa routine when the moment arrives.

Safety rails:
  * The latest wake time is a hard deadline: the alarm fires then even if
    Google is unreachable, the token expired, or no data ever arrived.
  * If the Alexa call fails it retries every 30 s until 10 min past the deadline.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import alexa, db, google_health
from .config import settings
from .planner import Snapshot, plan_wake, target_wake_date, wake_window

TZ = ZoneInfo(settings.timezone)


class _State:
    wake_date = None
    snap: Snapshot | None = None
    fetched_at: datetime | None = None
    warned_disconnected = False


S = _State()


async def tick(now: datetime | None = None) -> float:
    """Run one cycle. Returns how many seconds to sleep before the next one."""
    now = now or datetime.now(TZ)
    prefs = db.get_prefs()
    if not prefs.enabled:
        return 60
    wd = target_wake_date(now, prefs, TZ)
    if wd is None or wd.weekday() not in prefs.days:
        return 60
    key = wd.isoformat()
    night = db.get_night(key)
    if night and night["fired_at"]:
        return 60

    if S.wake_date != wd:
        S.wake_date, S.snap, S.fetched_at, S.warned_disconnected = wd, None, None, False

    if S.fetched_at is None or now - S.fetched_at >= timedelta(minutes=prefs.poll_minutes):
        S.fetched_at = now
        try:
            S.snap = await google_health.fetch_snapshot(now)
        except google_health.NotConnected:
            if not S.warned_disconnected:
                db.log("warn", "Google Health isn't connected; using your usual bedtime")
                S.warned_disconnected = True
        except Exception as e:  # keep the last good snapshot
            db.log("warn", f"Couldn't read sleep data: {e}")

    plan = plan_wake(now, wd, S.snap, prefs, TZ)
    w0, w1 = wake_window(wd, prefs, TZ)
    db.save_night(key, plan.wake_at.isoformat(), plan.confidence, plan.reason, w0.isoformat(), w1.isoformat())

    if now >= plan.wake_at or now >= w1:
        if now > w1 + timedelta(minutes=10):
            db.log("error", f"Missed the alarm for {key} (server was offline or Alexa kept failing)")
            db.mark_fired(key, False)
            return 60
        ok = await alexa.trigger_routine()
        if ok:
            db.mark_fired(key, True)
            return 60
        return 30

    return max(5.0, min(prefs.poll_minutes * 60, (plan.wake_at - now).total_seconds()))


async def run_forever() -> None:
    while True:
        try:
            delay = await tick()
        except Exception as e:
            db.log("error", f"Scheduler error: {e}")
            delay = 60
        await asyncio.sleep(delay)
