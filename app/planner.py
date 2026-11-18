"""Wake-time planning. Pure functions only: no network, no database.

You give a wake window (earliest/latest). Inside it we pick the moment closest
to the end of a sleep cycle (when you're most likely in light sleep) while
keeping you near your target sleep. In the last stretch before the alarm we
nudge the time using live sleep stages, if they're fresh enough to trust.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from statistics import median

ASLEEP_KINDS = {"light", "deep", "rem", "asleep"}


@dataclass
class Stage:
    start: datetime
    end: datetime
    kind: str  # awake | light | deep | rem | asleep


@dataclass
class Snapshot:
    onset: datetime | None
    stages: list[Stage]
    data_through: datetime | None  # newest moment the data covers


@dataclass
class Prefs:
    enabled: bool = True
    days: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])  # weekday of the wake-up morning, Mon=0
    earliest: str = "06:30"
    latest: str = "07:30"
    target_hours: float = 8.0
    cycle_minutes: int = 90
    typical_bedtime: str = "22:30"
    monitor_from: str = "21:30"
    sleep_latency_min: int = 15
    adjust_live: bool = True
    poll_minutes: int = 10


@dataclass
class Plan:
    wake_at: datetime
    confidence: str  # "observed" (real sleep data) or "estimated" (usual bedtime)
    reason: str


def hm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def wake_window(wake_date: date, prefs: Prefs, tz: tzinfo) -> tuple[datetime, datetime]:
    return (
        datetime.combine(wake_date, hm(prefs.earliest), tzinfo=tz),
        datetime.combine(wake_date, hm(prefs.latest), tzinfo=tz),
    )


def target_wake_date(now: datetime, prefs: Prefs, tz: tzinfo) -> date | None:
    """Which morning are we working toward right now? None = nothing to do."""
    today = now.date()
    if now.time() >= hm(prefs.monitor_from):
        return today + timedelta(days=1)
    _, latest = wake_window(today, prefs, tz)
    if now <= latest + timedelta(minutes=10):
        return today
    return None


def estimate_cycle(stages: list[Stage], default: int) -> int:
    """Median gap between deep-sleep bouts, if we've seen enough of them."""
    bouts: list[datetime] = []
    for t in sorted(s.start for s in stages if s.kind == "deep"):
        if not bouts or (t - bouts[-1]).total_seconds() / 60 > 25:
            bouts.append(t)
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(bouts, bouts[1:])]
    gaps = [g for g in gaps if 60 <= g <= 130]
    return round(median(gaps)) if gaps else default


def awake_minutes(stages: list[Stage]) -> float:
    return sum((s.end - s.start).total_seconds() / 60 for s in stages if s.kind == "awake")


def plan_wake(now: datetime, wake_date: date, snap: Snapshot | None, prefs: Prefs, tz: tzinfo) -> Plan:
    w0, w1 = wake_window(wake_date, prefs, tz)
    observed = snap is not None and snap.onset is not None

    if observed:
        onset = snap.onset  # type: ignore[union-attr]
        cycle = estimate_cycle(snap.stages, prefs.cycle_minutes)  # type: ignore[union-attr]
        awake = awake_minutes(snap.stages)  # type: ignore[union-attr]
    else:
        bed = datetime.combine(wake_date - timedelta(days=1), hm(prefs.typical_bedtime), tzinfo=tz)
        onset = bed + timedelta(minutes=prefs.sleep_latency_min)
        cycle = prefs.cycle_minutes
        awake = 0.0

    target = prefs.target_hours * 60

    def slept(t: datetime) -> float:
        return (t - onset).total_seconds() / 60 - awake

    def phase_gap(t: datetime) -> float:
        r = ((t - onset).total_seconds() / 60) % cycle
        return min(r, cycle - r)

    best_cost, wake = None, w0
    t = w0
    while t <= w1:
        cost = phase_gap(t) + 0.6 * max(0, target - slept(t)) + 0.1 * max(0, slept(t) - target)
        if best_cost is None or cost < best_cost:
            best_cost, wake = cost, t
        t += timedelta(minutes=5)

    source = "your sleep data" if observed else "your usual bedtime"
    reason = (
        f"Near the end of a {cycle}-minute sleep cycle, about {slept(wake) / 60:.1f} h of sleep "
        f"(from {source})."
    )
    plan = Plan(wake_at=wake, confidence="observed" if observed else "estimated", reason=reason)

    # Live nudges: only with fresh stage data.
    fresh = (
        observed
        and prefs.adjust_live
        and snap.data_through is not None  # type: ignore[union-attr]
        and snap.stages  # type: ignore[union-attr]
        and timedelta(0) <= now - snap.data_through <= timedelta(minutes=20)  # type: ignore[union-attr]
    )
    if fresh:
        current = snap.stages[-1].kind  # type: ignore[union-attr]
        minutes_to_alarm = (plan.wake_at - now).total_seconds() / 60
        if current == "deep" and minutes_to_alarm <= 30 and plan.wake_at < w1:
            plan.wake_at = min(max(plan.wake_at, now) + timedelta(minutes=15), w1)
            plan.reason = "You're in deep sleep close to your alarm, so it moved 15 minutes later."
        elif (
            current in {"light", "rem", "awake"}
            and w0 <= now <= w1
            and now < plan.wake_at
            and slept(now) >= target
        ):
            plan.wake_at = now + timedelta(minutes=2)
            plan.reason = "You're in light sleep and have reached your target, so the alarm is moving up."
    return plan
