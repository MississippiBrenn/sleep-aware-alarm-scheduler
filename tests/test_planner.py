from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.planner import Prefs, Snapshot, Stage, estimate_cycle, plan_wake, target_wake_date

TZ = ZoneInfo("America/Los_Angeles")
D = date(2026, 11, 3)


def at(h, m, day=D):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)


def test_estimated_plan_stays_in_window_and_picks_expected_time():
    p = Prefs(target_hours=8)
    plan = plan_wake(at(23, 0, D - timedelta(days=1)), D, None, p, TZ)
    assert plan.confidence == "estimated"
    assert plan.wake_at == at(7, 30)


def test_shorter_target_wakes_earlier():
    p = Prefs(target_hours=7)
    plan = plan_wake(at(23, 0, D - timedelta(days=1)), D, None, p, TZ)
    assert plan.wake_at == at(6, 30)


def test_cycle_estimate_from_deep_bouts():
    base = at(23, 30, D - timedelta(days=1))
    stages = [Stage(base + timedelta(minutes=m), base + timedelta(minutes=m + 20), "deep") for m in (0, 90, 185)]
    assert 88 <= estimate_cycle(stages, 100) <= 95
    assert estimate_cycle([], 100) == 100


def test_deep_sleep_near_alarm_pushes_it_back():
    p = Prefs(target_hours=7)
    onset = at(22, 45, D - timedelta(days=1))
    snap = Snapshot(onset, [Stage(at(6, 30), at(6, 50), "deep")], at(6, 50))
    plan = plan_wake(at(6, 55), D, snap, p, TZ)
    assert plan.wake_at == at(7, 10)


def test_light_sleep_after_target_pulls_alarm_forward():
    p = Prefs(target_hours=8)
    onset = at(22, 45, D - timedelta(days=1))
    snap = Snapshot(onset, [Stage(at(6, 40), at(6, 48), "light")], at(6, 48))
    plan = plan_wake(at(6, 50), D, snap, p, TZ)
    assert plan.wake_at == at(6, 52)


def test_stale_data_is_ignored_for_live_nudges():
    p = Prefs(target_hours=8)
    onset = at(22, 45, D - timedelta(days=1))
    snap = Snapshot(onset, [Stage(at(5, 0), at(5, 20), "deep")], at(5, 20))
    plan = plan_wake(at(6, 50), D, snap, p, TZ)
    assert plan.wake_at == at(7, 30)


def test_target_wake_date():
    p = Prefs()
    assert target_wake_date(at(22, 0, D - timedelta(days=1)), p, TZ) == D
    assert target_wake_date(at(3, 0), p, TZ) == D
    assert target_wake_date(at(12, 0), p, TZ) is None
