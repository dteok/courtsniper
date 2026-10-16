"""Orchestration: probe (never books) and book (full flow)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .client import (
    ConfirmPage,
    MyCourtsClient,
    Outcome,
    classify_result,
    format_dt,
    parse_confirm,
    shows_my_booking,
    visible_text,
)
from .config import Settings
from .notify import Notifier
from .scheduler import now_in, release_moment, target_date, wait_until
from .slots import Slot, SlotLookupError, SlotTable

log = logging.getLogger(__name__)

# Club rule 2.1: at most 1.5 hours (3 x 30 min) when booking in advance.
MAX_ADVANCE_SLOTS = 3
PARTNER_REMINDER = "Add partner name on MyCourts."
# One SMS segment is 160 GSM characters; Twilio trial accounts prefix ~38 characters.
SMS_MAX_CHARS = 120


@dataclass
class BookingReport:
    day: date
    slots: list[Slot]
    outcome: Outcome
    detail: str
    pages: list[Path] = field(default_factory=list)

    def summary(self) -> str:
        """Short, human-readable summary of the booking attempt."""
        s = self.slots
        when = f"{s[0].time}-{len(s)}x30min"
        return (
            f"CourtSniper {self.outcome.value.upper()}: court {s[0].court} "
            f"{format_dt(self.day)} {when}. {self.detail}"
        )

    def sms(self) -> str:
        """Short, plain-ASCII text that fits in one SMS segment."""
        first, last = self.slots[0], self.slots[-1]
        hours, minutes = map(int, last.time.split(":"))
        end = f"{hours + (minutes + 30) // 60:02d}:{(minutes + 30) % 60:02d}"
        text = (
            f"CourtSniper {self.outcome.value.upper()}: Court {first.court} "
            f"{self.day:%a %d %b}, {first.time}-{end}."
        )
        return text.encode("ascii", "ignore").decode()[:SMS_MAX_CHARS]


class RunRecorder:
    """Saves each page fetched during a run to timestamp for later inspection."""

    def __init__(self, root: Path, now: datetime):
        """Create a new run recorder, saving to a timestamped subdirectory of root."""

        self.dir = root / now.strftime("%Y%m%d-%H%M%S")
        self.saved: list[Path] = []

    def save(self, name: str, html: str) -> None:
        """Save the given HTML to a file in the run directory."""

        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / name
        path.write_text(html, encoding="utf-8")
        self.saved.append(path)
        log.debug("saved %s", path)


def _resolve(settings: Settings, table: SlotTable) -> list[Slot]:
    """Look up the target slots in the table, fail if not found or inferred.
    Args:
        settings (Settings): The settings for the booking.
        table (SlotTable): The table of available slots.
    Returns:
        list[Slot]: The list of resolved slots.
    """
    t = settings.target
    if settings.lead_days > 0 and t.slots > MAX_ADVANCE_SLOTS:
        raise SlotLookupError(
            f"{t.slots} slots exceeds the club's 1.5-hour advance limit (rule 2.1)"
        )
    slots = table.consecutive(t.court, t.start, t.slots)
    for s in slots:
        if s.status != "observed":
            log.warning(
                "court %s %s -> %s is inferred, not captured", s.court, s.time, s.ctid
            )
    log.info(
        "target: court %s, %s", t.court, ", ".join(f"{s.time}={s.ctid}" for s in slots)
    )
    return slots


def _login(settings: Settings, client: MyCourtsClient) -> None:
    """
    Log in and check the server clock offset.
    Args:
        settings (Settings): The settings for the booking.
        client (MyCourtsClient): The client for interacting with the courts.
    Returns:
        None
    """
    client.login(settings.username, settings.password)
    log.info("logged in")
    offset = client.server_clock_offset()
    if offset is not None:
        log.info("server clock offset: %+.0fs", offset)
        if abs(offset) >= 2:
            log.warning(
                "server and local clocks differ by %+.0fs; check your Mac's time sync",
                offset,
            )


def check_confirm(page: ConfirmPage | None, slots: list[Slot], day: date) -> str | None:
    """Return a reason not to commit, or None if the page matches what we asked for.
    Args:
        page (ConfirmPage | None): The parsed confirm page, or None if not found.
        slots (list[Slot]): The list of slots to check.
        day (date): The day of the booking.
    Returns:
        str | None: A reason not to commit, or None if the page is valid.
    """
    if page is None:
        return "No booking form on confirm page (slot taken or not bookable)."
    if page.vc is None:
        return "No vc token on confirm page."
    expected_ctids = [s.ctid for s in slots]
    if page.ct_ids != [s.ctid for s in slots]:
        return f"Confirming listing slots {page.ct_ids}, expected {expected_ctids}."
    if page.fields.get("dt") != format_dt(day):
        return (
            f"Confirm page date {page.fields.get('dt')!r}, expected {format_dt(day)!r}."
        )
    if not page.shows_court(slots[0].court):
        return (
            f"Confirm page does not show court {slots[0].court}: {page.summary[:80]!r}"
        )
    return None


def probe(
    settings: Settings, table: SlotTable, client: MyCourtsClient, runs_dir: Path
) -> str | None:
    """Log in, save the grid and the confirm page, report vc. Never books.

    Args:
        settings (Settings): The settings for the booking.
        table (SlotTable): The table of slots to probe.
        client (MyCourtsClient): The client for interacting with the courts.
        runs_dir (Path): The directory for saving run data.
    Returns:
        str | None: The vc token if the confirm page is valid, else None.
    """
    now = now_in(settings.timezone)
    slots = _resolve(settings, table)
    day = target_date(now, settings.lead_days)
    recorder = RunRecorder(runs_dir, now)

    _login(settings, client)
    recorder.save(
        f"grid_d{settings.lead_days}.html", client.booking_grid(settings.lead_days)
    )
    confirm = client.open_confirm([s.ctid for s in slots], day)
    recorder.save("confirm_page.html", confirm)

    page = parse_confirm(confirm)
    problem = check_confirm(page, slots, day)
    if problem:
        log.warning("%s Page text: %s", problem, visible_text(confirm)[:300])
    else:
        log.info("confirm page OK: %s | vc=%s", page.summary, page.vc)
    log.info("probe done, nothing booked; pages in %s", recorder.dir)
    return page.vc if page else None


def book(  # pylint: disable=too-many-locals
    settings: Settings,
    table: SlotTable,
    client: MyCourtsClient,
    notifier: Notifier,
    runs_dir: Path,
) -> BookingReport:
    """Log in, wait for release, then confirm and commit the booking.

    Args:
        settings (Settings): The settings for the booking.
        table (SlotTable): The table of slots to book.
        client (MyCourtsClient): The client for interacting with the courts.
        notifier (Notifier): The notifier for sending alerts.
        runs_dir (Path): The directory for saving run data.

    Returns:
        BookingReport: The report of the booking attempt. Sends SMS.

    """
    slots = _resolve(settings, table)  # fail fast before touching the site
    now = now_in(settings.timezone)
    day = target_date(now, settings.lead_days)
    recorder = RunRecorder(runs_dir, now)
    ct_ids = [s.ctid for s in slots]

    _login(settings, client)

    moment = release_moment(now, settings.release_time, settings.fire_delay_ms)
    if now_in(settings.timezone) >= moment:
        log.warning(
            "release time %s already passed today; booking now", settings.release_time
        )
    else:
        log.info("waiting for %s", moment.strftime("%H:%M:%S.%f")[:-3])
        wait_until(moment, lambda: now_in(settings.timezone))
    log.info("go")

    confirm = client.open_confirm(ct_ids, day)
    recorder.save("confirm_page.html", confirm)
    page = parse_confirm(confirm)
    problem = check_confirm(page, slots, day)
    if problem:
        log.warning(
            "not committing: %s Page text: %s", problem, visible_text(confirm)[:300]
        )
        report = BookingReport(day, slots, Outcome.FAILED, problem, recorder.saved)
    else:
        log.info("confirm page OK: %s | vc=%s, committing", page.summary, page.vc)
        result = client.commit(page)
        recorder.save("result_page.html", result)
        if shows_my_booking(result, day, slots[0].court):
            outcome, detail = Outcome.SUCCESS, PARTNER_REMINDER
        else:
            outcome = classify_result(result)
            detail = f"Check MyCourts. Page: {visible_text(result)[:60]}"
        report = BookingReport(day, slots, outcome, detail, recorder.saved)

    log.info(report.summary())
    if report.outcome is not Outcome.FAILED or settings.notify_on_failure:
        notifier.send(report.sms())
    return report
