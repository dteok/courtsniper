"""Tests for courtsniper.client."""

from datetime import date
from pathlib import Path

import pytest

from courtsniper.client import (
    UnsafeUrlError,
    Outcome,
    classify_result,
    extract_vc,
    find_login_form,
    format_dt,
    parse_confirm,
    shows_my_booking,
)

LOGIN_PAGE = """
<form action="login_check.asp" method="post">
  <input type="hidden" name="token" value="abc">
  <input type="text" name="username"><input type="password" name="password">
  <input type="checkbox" name="remember"><input type="submit" value="Log in">
</form>"""


def test_format_dt_has_no_leading_zero():
    """The site writes days without a leading zero."""
    assert format_dt(date(2026, 10, 9)) == "9 October 2026"
    assert format_dt(date(2026, 10, 12)) == "12 October 2026"


def test_find_login_form():
    """Login form parsing keeps only safe fields and the same-origin action."""
    form = find_login_form(LOGIN_PAGE, "https://club.example/")
    assert form.action == "https://club.example/login_check.asp"
    assert (form.username_field, form.password_field) == ("username", "password")
    # Only the allow-listed CSRF-style hidden input survives.
    assert form.hidden_fields == {"token": "abc"}


def test_find_login_form_drops_planted_fields():
    """A page-planted input that is not a known token name is never echoed."""
    page = LOGIN_PAGE.replace(
        '<input type="checkbox" name="remember">',
        '<input type="hidden" name="evil" value="x">'
        '<input type="hidden" name="password_hint" value="y">',
    )
    form = find_login_form(page, "https://club.example/")
    assert form.hidden_fields == {"token": "abc"}


def test_find_login_form_rejects_off_origin_action():
    """Credentials must never be posted to another host (SSRF/exfil defence)."""
    page = LOGIN_PAGE.replace('action="login_check.asp"', 'action="https://evil.test/x"')
    with pytest.raises(UnsafeUrlError):
        find_login_form(page, "https://club.example/")


def test_find_login_form_rejects_downgraded_scheme():
    """An http: action on an https page is refused outright."""
    page = LOGIN_PAGE.replace('action="login_check.asp"', 'action="http://club.example/"')
    with pytest.raises(UnsafeUrlError):
        find_login_form(page, "https://club.example/")


def test_no_login_form_when_logged_in():
    """Pages without a password form return None."""
    assert find_login_form("<a href='logout.asp'>Log out</a>", "https://x/") is None


REAL_CONFIRM = (Path(__file__).parent / "fixtures" / "confirm_court7.html").read_text(
    encoding="utf-8"
)


def test_parse_real_confirm_page():
    """The real fixture's obfuscated vc token, date and slot ids are parsed."""
    page = parse_confirm(REAL_CONFIRM)
    assert page.vc == "81F2C"  # written by obfuscated document.write
    assert page.fields["dt"] == "16 October 2026"
    assert page.ct_ids == ["26761", "26762", "26763"]
    assert page.shows_court(7)
    assert not page.shows_court(1)  # "Court 7" must not match court 1 or 17


def test_extract_vc_variants():
    """vc tokens are found in plain inputs and document.write-obfuscated ones."""
    plain = (
        '<form action="bookings_process_multi.asp"><input type="hidden" '
        'name="vc" value="A5215"></form>'
    )
    assert extract_vc(plain) == "A5215"
    js = (
        """<form action="bookings_process_multi.asp"><script>"""
        """document.write("<inp"+'ut name="vc" value="B77'+'31">')</script></form>"""
    )
    assert extract_vc(js) == "B7731"
    assert extract_vc("<p>Court already booked</p>") is None


def test_classify_result():
    """Outcome comes from visible text; ambiguous pages are UNVERIFIED."""
    assert classify_result("<h1>Booking confirmed</h1>") is Outcome.SUCCESS
    assert (
        classify_result("<p>Sorry, this court has already been booked</p>")
        is Outcome.FAILED
    )
    assert classify_result("<p>Welcome</p>") is Outcome.UNVERIFIED
    # text inside scripts is ignored
    assert (
        classify_result("<script>'sorry'</script><p>Booking confirmed</p>")
        is Outcome.SUCCESS
    )
    # a page that claims both success and failure is never trusted
    assert (
        classify_result("<p>Booking confirmed. Sorry.</p>") is Outcome.UNVERIFIED
    )


def test_real_result_page_shows_booking():
    """Booked-by-me blocks must match the exact day and court number."""
    html = (Path(__file__).parent / "fixtures" / "result_court7.html").read_text(
        encoding="utf-8"
    )
    assert shows_my_booking(html, date(2026, 10, 16), 7)
    assert not shows_my_booking(html, date(2026, 10, 16), 1)
    assert not shows_my_booking(html, date(2026, 10, 17), 7)
