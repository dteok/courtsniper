"""Command line entry point.

python -m courtsniper probe                      # never books
python -m courtsniper book                       # waits for 07:15, then books
python -m courtsniper probe --court 2 --start 15:00 --slots 2 --days 3
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from pathlib import Path

from .booker import book, probe
from .client import LoginError, MyCourtsClient, Outcome
from .config import ConfigError, load_settings
from .notify import build_notifier
from .slots import SlotLookupError, SlotTable

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="courtsniper",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("mode", choices=["probe", "book"])
    p.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.toml")
    p.add_argument(
        "--slots-table", type=Path, default=PROJECT_ROOT / "data" / "court_ctids.csv"
    )
    p.add_argument("--runs-dir", type=Path, default=PROJECT_ROOT / "runs")
    p.add_argument("--court", type=int, help="override target court")
    p.add_argument("--start", help="override first slot, HH:MM")
    p.add_argument("--slots", type=int, help="override number of 30-min slots")
    p.add_argument("--days", type=int, help="override lead days (testing)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,  # Cloud Logging treats stderr lines as errors
    )
    for noisy in (
        "urllib3",
        "twilio",
    ):  # twilio logs full requests, incl. the account SID
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        settings = load_settings(args.config).with_target(
            court=args.court, start=args.start, slots=args.slots
        )
        if args.days is not None:
            settings = replace(settings, lead_days=args.days)
        table = SlotTable.from_csv(args.slots_table)
    except (ConfigError, SlotLookupError, OSError) as exc:
        logging.error("%s", exc)
        return 2

    client = MyCourtsClient(settings.base_url)
    notifier = build_notifier(settings)
    try:
        if args.mode == "probe":
            probe(settings, table, client, args.runs_dir)
            return 0
        report = book(settings, table, client, notifier, args.runs_dir)
        return 0 if report.outcome is Outcome.SUCCESS else 1
    except (SlotLookupError, LoginError) as exc:
        logging.error("%s", exc)
        error = str(exc)
    # a failed SMS must not abort a booking
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logging.exception("unexpected error")
        error = f"{type(exc).__name__}: {exc}"
    if args.mode == "book" and settings.notify_on_failure:
        notifier.send(
            f"CourtSniper ERROR: {error}".encode("ascii", "ignore").decode()[:120]
        )
    return 3


if __name__ == "__main__":
    sys.exit(main())
