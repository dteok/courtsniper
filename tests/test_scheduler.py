"""tests for the scheduler module"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from courtsniper.scheduler import release_moment, target_date, wait_until

LONDON = ZoneInfo("Europe/London")


def test_target_date_is_lead_days_ahead():
    now = datetime(2026, 10, 9, 7, 14, tzinfo=LONDON)
    assert str(target_date(now, 4)) == "2026-10-13"


def test_release_moment_with_delay():
    now = datetime(2026, 10, 9, 7, 0, tzinfo=LONDON)
    expected = datetime(2026, 10, 9, 7, 15, 0, 250000, tzinfo=LONDON)
    assert release_moment(now, time(7, 15), 250) == expected


def test_wait_until_sleeps_then_spins():
    clock = [datetime(2026, 10, 9, 7, 14, 50, tzinfo=LONDON)]
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        clock[0] += timedelta(seconds=seconds)

    target = datetime(2026, 10, 9, 7, 15, tzinfo=LONDON)
    wait_until(target, lambda: clock[0], sleep)
    assert slept[0] == 9  # coarse sleep to T-1s
    assert clock[0] >= target  # never returns early
    assert clock[0] - target < timedelta(milliseconds=5)


def test_wait_until_past_returns_immediately():
    now = datetime(2026, 10, 9, 8, 0, tzinfo=LONDON)
    assert wait_until(now - timedelta(hours=1), lambda: now, lambda s: None) == 0.0
