"""Lookup of slot ids (ctIDs) by court and time.

A ctID identifies a court and a time of day; it is the same on every date.
The date is sent separately when booking.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

SLOT_MINUTES = 30


class SlotLookupError(Exception):
    """Raised when the requested court/time/length cannot be resolved."""


@dataclass(frozen=True)
class Slot:
    court: int
    surface: str
    time: str
    ctid: str
    status: str  # "observed" (captured) or "inferred" (from a confirmed run)


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


class SlotTable:
    def __init__(self, slots: list[Slot]):
        self._by_court: dict[int, list[Slot]] = {}
        for slot in sorted(slots, key=lambda s: (s.court, _minutes(s.time))):
            self._by_court.setdefault(slot.court, []).append(slot)

    @classmethod
    def from_csv(cls, path: Path) -> SlotTable:
        with open(path, newline="", encoding="utf-8") as f:
            return cls(
                [
                    Slot(
                        int(r["court"]), r["surface"], r["time"], r["ctid"], r["status"]
                    )
                    for r in csv.DictReader(f)
                ]
            )

    @property
    def courts(self) -> list[int]:
        return sorted(self._by_court)

    def consecutive(self, court: int, start: str, count: int) -> list[Slot]:
        """Return `count` back-to-back slots on `court`, starting at `start`."""
        if count < 1:
            raise SlotLookupError("Number of slots must be at least 1")
        day = self._by_court.get(court)
        if day is None:
            raise SlotLookupError(f"Unknown court {court}. Known: {self.courts}")
        times = [s.time for s in day]
        if start not in times:
            raise SlotLookupError(
                f"{start} is not a slot on court {court}. Valid: {', '.join(times)}"
            )

        first = times.index(start)
        picked = day[first: first + count]
        if len(picked) < count:
            raise SlotLookupError(
                f"Only {len(picked)} slot(s) left on court {court} from {start}"
            )
        for prev, nxt in pairwise(picked):
            if _minutes(nxt.time) - _minutes(prev.time) != SLOT_MINUTES:
                raise SlotLookupError(
                    f"Gap between {prev.time} and {nxt.time} on court {court}"
                )
        return picked
