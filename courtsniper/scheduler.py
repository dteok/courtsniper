"""Release-time calculation and a precise wait."""

from __future__ import annotations

import time as _time
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def target_date(now: datetime, lead_days: int) -> date:
    return (now + timedelta(days=lead_days)).date()


def release_moment(now: datetime, release: time, delay_ms: int = 0) -> datetime:
    """Today's release time (in now's timezone) plus a small safety delay."""
    moment = datetime.combine(now.date(), release, tzinfo=now.tzinfo)
    return moment + timedelta(milliseconds=delay_ms)


def now_in(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def wait_until(
    moment: datetime,
    now_fn: Callable[[], datetime],
    sleep_fn: Callable[[float], None] = _time.sleep,
) -> float:
    """Sleep until `moment`: coarse sleep, then a short spin. Returns seconds waited.
    Args:
        moment (datetime): The target time to wait until.
        now_fn (Callable[[], datetime]): Function returning the current time.
        sleep_fn (Callable[[float], None]): Function to sleep for a given number of seconds.
    Returns:
        float: The number of seconds waited.
    """
    remaining = (moment - now_fn()).total_seconds()
    if remaining <= 0:
        return 0.0
    if remaining > 1:
        sleep_fn(remaining - 1)
    while now_fn() < moment:
        sleep_fn(0.002)
    return remaining
