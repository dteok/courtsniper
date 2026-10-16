"""Flow tests with a fake client: no network."""

from datetime import time
from pathlib import Path

import pytest

from courtsniper.slots import SlotLookupError
from courtsniper.booker import book, probe
from courtsniper.client import Outcome, format_dt
from courtsniper.config import Settings, Target
from courtsniper.scheduler import now_in, target_date
from courtsniper.slots import SlotTable

TABLE = SlotTable.from_csv(Path(__file__).parent.parent / "data" / "court_ctids.csv")


def make_settings(**kw):
    base = dict(  # pylint: disable=use-dict-literal
        base_url="https://club.example",
        timezone="Europe/London",
        username="u",
        password="p",
        target=Target(court=2, start="15:00", slots=2),
        lead_days=4,
        release_time=time(0, 0),
        fire_delay_ms=0,  # already passed: runs immediately
        twilio_sid=None,
        twilio_token=None,
        twilio_from=None,
        notify_to=None,
        notify_on_failure=True,
    )
    return Settings(**{**base, **kw})


class FakeClient:
    def __init__(self, confirm_html, result_html=None):
        day = target_date(now_in("Europe/London"), 4)
        self.confirm_html = confirm_html
        self.result_html = result_html or (
            f'<div class="my_booking booked_by_me"><strong>{day:%A} {format_dt(day)} '
            f"1500, 1530 hrs Clay Court 2</strong></div>"
        )
        self.committed = None

    def login(self, _user, _pw):
        pass

    def server_clock_offset(self):
        return 0.0

    def booking_grid(self, _days):
        return "<table></table>"

    def open_confirm(self, _ct_ids, _day):
        return self.confirm_html

    def commit(self, page):
        self.committed = page.fields
        return self.result_html


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


def confirm_page(ct_ids="28169,28170", dt=None, court=2):
    dt = dt or format_dt(target_date(now_in("Europe/London"), 4))
    return (
        f'<form action="bookings_process_multi.asp">'
        f"<script>document.write('<inp'+'ut name=\"vc\" value=\"A52'+'15\">')</script>"
        f'<input name="dt" type="hidden" value="{dt}">'
        f'<strong>Clay Court {court}</strong><input name="ctid_csv" type="hidden" value="{ct_ids}">'
        f"</form>"
    )


VC_PAGE = confirm_page()


def test_book_success(tmp_path):
    client, notifier = FakeClient(VC_PAGE), FakeNotifier()
    report = book(make_settings(), TABLE, client, notifier, tmp_path)
    assert report.outcome is Outcome.SUCCESS
    assert client.committed["vc"] == "A5215"
    assert client.committed["ctid_csv"] == "28169,28170"
    assert "SUCCESS" in notifier.sent[0]
    assert {p.name for p in report.pages} == {"confirm_page.html", "result_page.html"}


def test_book_without_vc_never_commits(tmp_path):
    client, notifier = FakeClient("<p>already booked</p>"), FakeNotifier()
    report = book(make_settings(), TABLE, client, notifier, tmp_path)
    assert report.outcome is Outcome.FAILED
    assert client.committed is None
    assert "FAILED" in notifier.sent[0]


def test_failure_sms_can_be_disabled(tmp_path):
    notifier = FakeNotifier()
    book(
        make_settings(notify_on_failure=False),
        TABLE,
        FakeClient("<p>x</p>"),
        notifier,
        tmp_path,
    )
    assert notifier.sent == []  # pylint: disable=use-implicit-booleaness-not-comparison


def test_probe_never_commits(tmp_path):
    client = FakeClient(VC_PAGE)
    assert probe(make_settings(), TABLE, client, tmp_path) == "A5215"
    assert client.committed is None


def test_mismatched_confirm_page_is_not_committed(tmp_path):
    for page in (
        confirm_page(ct_ids="28169"),
        confirm_page(court=1),
        confirm_page(dt="1 January 2030"),
    ):
        client = FakeClient(page)
        report = book(make_settings(), TABLE, client, FakeNotifier(), tmp_path)
        assert report.outcome is Outcome.FAILED
        assert client.committed is None


def test_more_than_90_minutes_refused(tmp_path):
    client = FakeClient(VC_PAGE)
    with pytest.raises(SlotLookupError, match="1.5-hour"):
        book(
            make_settings(target=Target(court=2, start="15:00", slots=4)),
            TABLE,
            client,
            FakeNotifier(),
            tmp_path,
        )
    assert client.committed is None


# def test_success_sms_reminds_partner_name(tmp_path):
#     notifier = FakeNotifier()
#     book(make_settings(), TABLE, FakeClient(VC_PAGE), notifier, tmp_path)
#     assert "partner" in notifier.sent[0]


def test_sms_fits_one_segment_and_is_ascii(tmp_path):
    notifier = FakeNotifier()
    noisy = "<p>Your Bookings \u2261 Menu \u00a321.00 " + "x" * 400 + "</p>"
    book(
        make_settings(),
        TABLE,
        FakeClient(VC_PAGE, result_html=noisy),
        notifier,
        tmp_path,
    )
    sms = notifier.sent[0]
    assert len(sms) <= 120 and sms.isascii()
    assert "UNVERIFIED" in sms
