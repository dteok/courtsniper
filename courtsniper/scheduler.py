"""Release-time calculation and a precise wait.

The club opens slots at a fixed local time; we sleep coarsely, then spin with
an adaptive interval so the fire moment is hit with millisecond accuracy while
burning minimal CPU during the long coarse wait.
"""

from __future__ import annotations

import time as _time
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# Spin intervals: coarse just before the moment, fine at the last second.
_COARSE_POLL_S = 0.01
_FINE_POLL_S = 0.001
_FINE_WINDOW_S = 1.0


def target_date(now: datetime, lead_days: int) -> date:
    """The day to book: ``lead_days`` after ``now``'s date.

    Args:
        now: Current (or reference) time, timezone-aware.
        lead_days: Days in advance the club releases slots.

    Returns:
        date: The booking date.
    """
    return (now + timedelta(days=lead_days)).date()


def release_moment(now: datetime, release: time, delay_ms: int = 0) -> datetime:
    """Today's release time (in now's timezone) plus a small safety delay.

    Args:
        now: Reference datetime; its date and timezone are used.
        release: Local release time of day.
        delay_ms: Milliseconds to add so we never fire before the server.

    Returns:
        datetime: Timezone-aware fire moment.
    """
    moment = datetime.combine(now.date(), release, tzinfo=now.tzinfo)
    return moment + timedelta(milliseconds=delay_ms)


def now_in(tz_name: str) -> datetime:
    """Current time in the named timezone.

    Args:
        tz_name: IANA timezone name, e.g. ``"Europe/London"``.

    Returns:
        datetime: Timezone-aware "now".
    """
    return datetime.now(ZoneInfo(tz_name))


def wait_until(
    moment: datetime,
    now_fn: Callable[[], datetime],
    sleep_fn: Callable[[float], None] = _time.sleep,
) -> float:
    """Sleep until `moment`: coarse sleep, then an adaptive short spin.

    Never returns before ``moment``. Poll interval shrinks from 10 ms to 1 ms
    inside the final second for millisecond accuracy at low CPU cost.

    Args:
        moment: Target time to wait until (timezone-aware).
        now_fn: Function returning the current time (injectable for tests).
        sleep_fn: Sleep-for-N-seconds function (injectable for tests).

    Returns:
        float: Seconds that remained when the call started (0 if already past).
    """
    remaining = (moment - now_fn()).total_seconds()
    if remaining <= 0:
        return 0.0
    if remaining > 1:
        sleep_fn(remaining - 1)
    while now_fn() < moment:
        left = (moment - now_fn()).total_seconds()
        poll = _FINE_POLL_S if left < _FINE_WINDOW_S else _COARSE_POLL_S
        sleep_fn(poll)
    return remaining
