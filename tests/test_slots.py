"""tests for the slots module"""

from pathlib import Path

import pytest

from courtsniper.slots import SlotLookupError, SlotTable

TABLE = SlotTable.from_csv(Path(__file__).parent.parent / "data" / "court_ctids.csv")


@pytest.mark.parametrize(
    "court,start,n,expected",
    [
        (1, "12:00", 2, ["28143", "28144"]),  # first ever capture
        (2, "15:00", 3, ["28169", "28170", "28171"]),
        (
            10,
            "20:30",
            3,
            ["33885", "33886", "39857"],
        ),  # crosses into the later-added ids
        (8, "20:30", 3, ["33881", "33882", "39856"]),
        (1, "10:30", 2, ["39860", "28141"]),  # crosses id ranges
    ],
)
# pylint: disable=missing-module-docstring
def test_known_captures(court, start, n, expected):
    assert [s.ctid for s in TABLE.consecutive(court, start, n)] == expected


# pylint: disable=missing-module-docstring
def test_every_court_has_full_day():
    for court in TABLE.courts:
        slots = TABLE.consecutive(court, "08:00", 1)
        assert slots, court


# pylint: disable=missing-module-docstring
def test_ids_unique():
    ids = [s.ctid for c in TABLE.courts for s in TABLE.consecutive(c, "08:00", 26)]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "court,start,n",
    [
        (3, "12:00", 1),
        (1, "12:15", 1),
        (1, "20:30", 2),
        (1, "12:00", 0),
    ],
)
# pylint: disable=missing-module-docstring
def test_invalid_requests(court, start, n):
    with pytest.raises(SlotLookupError):
        TABLE.consecutive(court, start, n)
