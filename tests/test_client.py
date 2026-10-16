"""tests for courtsniper.client"""

from datetime import date
from pathlib import Path

from courtsniper.client import (
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


# pylint: disable=missing-module-docstring
def test_format_dt_has_no_leading_zero():
    assert format_dt(date(2026, 10, 9)) == "9 October 2026"
    assert format_dt(date(2026, 10, 12)) == "12 October 2026"


# pylint: disable=missing-module-docstring
def test_find_login_form():
    form = find_login_form(LOGIN_PAGE, "https://club.example/")
    assert form.action == "https://club.example/login_check.asp"
    assert (form.user_field, form.password_field) == ("username", "password")
    assert form.fields == {"token": "abc", "username": "", "password": ""}


# pylint: disable=missing-module-docstring
def test_no_login_form_when_logged_in():
    assert find_login_form("<a href='logout.asp'>Log out</a>", "https://x/") is None


REAL_CONFIRM = (Path(__file__).parent / "fixtures" / "confirm_court7.html").read_text(
    encoding="utf-8"
)


# pylint: disable=missing-module-docstring
def test_parse_real_confirm_page():
    page = parse_confirm(REAL_CONFIRM)
    assert page.vc == "81F2C"  # written by obfuscated document.write
    assert page.fields["dt"] == "16 October 2026"
    assert page.ct_ids == ["26761", "26762", "26763"]
    assert page.shows_court(7)
    assert not page.shows_court(1)  # "Court 7" must not match court 1 or 17


# pylint: disable=missing-module-docstring
def test_extract_vc_variants():
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


# pylint: disable=missing-module-docstring
def test_classify_result():
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


# pylint: disable=missing-module-docstring
def test_real_result_page_shows_booking():
    html = (Path(__file__).parent / "fixtures" / "result_court7.html").read_text(
        encoding="utf-8"
    )
    assert shows_my_booking(html, date(2026, 10, 16), 7)
    assert not shows_my_booking(html, date(2026, 10, 16), 1)
    assert not shows_my_booking(html, date(2026, 10, 17), 7)
