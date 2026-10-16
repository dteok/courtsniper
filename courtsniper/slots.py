"""Lookup of slot ids (ctIDs) by court and time.

A ctID identifies a court and a time of day; it is the same on every date.
The date is sent separately when booking.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

SLOT_MINUTES = 30
_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class SlotLookupError(Exception):
    """Raised when the requested court/time/length cannot be resolved."""


def _minutes(hhmm: str) -> int:
    """Convert ``HH:MM`` to minutes since midnight.

    Args:
        hhmm: Time string such as ``"08:30"``.

    Returns:
        int: Minutes past midnight.

    Raises:
        SlotLookupError: If the string is not a valid ``HH:MM`` time.
    """
    match = _HHMM_RE.match(hhmm.strip())
    if match is None:
        raise SlotLookupError(f"Not a valid HH:MM time: {hhmm!r}")
    return int(match.group(1)) * 60 + int(match.group(2))


@dataclass(frozen=True)
class Slot:
    """One bookable half-hour on one court.

    Attributes:
        court: Court number.
        surface: Court surface name (e.g. "clay").
        time: Slot start as ``HH:MM``.
        ctid: Site slot id, stable across dates.
        status: ``"observed"`` (captured) or ``"inferred"`` (derived).
    """

    court: int
    surface: str
    time: str
    ctid: str
    status: str  # "observed" (captured) or "inferred" (from a confirmed run)


@dataclass(frozen=True)
class _TimedSlot:
    """Internal: a slot with its precomputed minute offset."""

    slot: Slot
    minute: int


@dataclass(frozen=True)
class SlotTable:
    """Index of slots per court, sorted by time, for fast consecutive lookups."""

    _by_court: dict[int, list[_TimedSlot]] = field(default_factory=dict, repr=False)

    @classmethod
    def from_csv(cls, path: Path) -> SlotTable:
        """Load a slot table from a CSV file.

        Args:
            path: CSV with columns ``court,surface,time,ctid,status``.

        Returns:
            SlotTable: The indexed table.

        Raises:
            SlotLookupError: On malformed rows or bad times.
            OSError: If the file cannot be read.
        """
        try:
            with open(path, newline="", encoding="utf-8") as f:
                slots = [
                    Slot(
                        int(r["court"]),
                        r["surface"],
                        r["time"],
                        r["ctid"],
                        r["status"],
                    )
                    for r in csv.DictReader(f)
                ]
        except (KeyError, ValueError) as exc:
            raise SlotLookupError(f"Bad slot table {path}: {exc}") from exc
        return cls.from_slots(slots)

    @classmethod
    def from_slots(cls, slots: list[Slot]) -> SlotTable:
        """Build a table from Slot objects, validating times once up front.

        Args:
            slots: Slots to index.

        Returns:
            SlotTable: Indexed table ordered by court and time.

        Raises:
            SlotLookupError: If any slot time is invalid.
        """
        timed = sorted(
            (_TimedSlot(s, _minutes(s.time)) for s in slots),
            key=lambda t: (t.slot.court, t.minute),
        )
        by_court: dict[int, list[_TimedSlot]] = {}
        for entry in timed:
            by_court.setdefault(entry.slot.court, []).append(entry)
        return cls(_by_court=by_court)

    @property
    def courts(self) -> list[int]:
        """Court numbers present in the table, ascending."""
        return sorted(self._by_court)

    def consecutive(self, court: int, start: str, count: int) -> list[Slot]:
        """Return `count` back-to-back slots on `court`, starting at `start`.

        Args:
            court: Court number to book on.
            start: First slot time, ``HH:MM``.
            count: Number of 30-minute slots wanted.

        Returns:
            list[Slot]: The consecutive slots.

        Raises:
            SlotLookupError: Unknown court/time, too few slots left, a gap in
                coverage, count < 1, or an invalid ``HH:MM``.
        """
        if count < 1:
            raise SlotLookupError("Number of slots must be at least 1")
        day = self._by_court.get(court)
        if day is None:
            raise SlotLookupError(f"Unknown court {court}. Known: {self.courts}")
        wanted = _minutes(start)
        first = next((i for i, t in enumerate(day) if t.minute == wanted), None)
        if first is None:
            times = ", ".join(t.slot.time for t in day)
            raise SlotLookupError(
                f"{start} is not a slot on court {court}. Valid: {times}"
            )

        picked = day[first : first + count]
        if len(picked) < count:
            raise SlotLookupError(
                f"Only {len(picked)} slot(s) left on court {court} from {start}"
            )
        for prev, nxt in pairwise(picked):
            if nxt.minute - prev.minute != SLOT_MINUTES:
                raise SlotLookupError(
                    f"Gap between {prev.slot.time} and {nxt.slot.time} "
                    f"on court {court}"
                )
        return [t.slot for t in picked]
