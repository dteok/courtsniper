"""HTTP client for a mycourts.co.uk club site, plus pure HTML-parsing helpers.

Booking is two POSTs, mirroring what the browser does:
  1. bookings_confirm.asp       -> "slide to confirm" page, which carries a vc token
  2. bookings_process_multi.asp -> commits the booking (vc, dt, ctid_csv)
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
)

# Placeholders until we have seen real result pages. Matched case-insensitively
# against the page's visible text. Update these after the first real booking.
SUCCESS_PHRASES = (
    "booking confirmed",
    "successfully booked",
    "has been booked",
    "thank you for your booking",
    "booking complete",
)
FAILURE_PHRASES = (
    "already been booked",
    "already booked",
    "no longer available",
    "not available",
    "unable to",
    "sorry",
)


class LoginError(Exception):
    pass


class Outcome(enum.Enum):
    SUCCESS = "success"
    FAILED = "failed"
    UNVERIFIED = "unverified"  # page matched neither list: check the club site


@dataclass(frozen=True)
class LoginForm:
    action: str
    fields: dict[str, str]
    user_field: str
    password_field: str


# ---- pure helpers (tested offline) -------------------------------------------


def format_dt(day: date) -> str:
    """Date in the site's format, e.g. '12 October 2026'."""
    return f"{day.day} {day:%B %Y}"


def visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def find_login_form(html: str, page_url: str) -> LoginForm | None:
    """Locate the form containing a password field and read its fields."""
    soup = BeautifulSoup(html, "html.parser")
    pw = soup.find("input", attrs={"type": "password"})
    form = pw.find_parent("form") if pw else None
    if form is None:
        return None
    user = form.find("input", attrs={"type": re.compile(r"^(text|email)$", re.I)})
    if user is None or not user.get("name") or not pw.get("name"):
        return None
    fields = {
        el["name"]: el.get("value", "")
        for el in form.find_all("input")
        if el.get("name")
        and el.get("type", "text").lower()
        not in ("checkbox", "radio", "submit", "button")
    }
    return LoginForm(
        action=urljoin(page_url, form.get("action") or page_url),
        fields=fields,
        user_field=user["name"],
        password_field=pw["name"],
    )


@dataclass(frozen=True)
class ConfirmPage:
    """The 'Are you sure?' page: the fields its form would submit, and what it shows."""

    fields: dict[str, str]  # vc, dt, ctid_csv
    summary: str  # e.g. "Tennis Court 7 Friday 16 October 2026 800 hrs, ..."

    @property
    def vc(self) -> str | None:
        return self.fields.get("vc") or None

    @property
    def ct_ids(self) -> list[str]:
        return [c for c in self.fields.get("ctid_csv", "").split(",") if c]

    def shows_court(self, court: int) -> bool:
        return re.search(rf"\bCourt {court}\b", self.summary) is not None


_JS_STRING = re.compile(r"""'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)\"""")
_DOC_WRITE = re.compile(r"document\.write\((.*?)\)\s*;?", re.S)


def _expand_document_write(script: str) -> str:
    """Join string literals in `document.write('<'+'inp'+...)` into the HTML
    it writes.
    """
    parts = []
    for call in _DOC_WRITE.findall(script):
        parts.extend(a if a or not b else b for a, b in _JS_STRING.findall(call))
    return "".join(parts)


def parse_confirm(html: str) -> ConfirmPage | None:
    """Read the booking form, including inputs the page writes with JavaScript."""
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", action=re.compile(r"bookings_process_multi", re.I))
    if form is None:
        return None
    fields: dict[str, str] = {}
    for script in form.find_all("script"):
        written = BeautifulSoup(
            _expand_document_write(script.get_text()), "html.parser"
        )
        fields.update(
            {
                el["name"]: el.get("value", "")
                for el in written.find_all("input")
                if el.get("name")
            }
        )
    fields.update(
        {
            el["name"]: el.get("value", "")
            for el in form.find_all("input")
            if el.get("name") and el.get("type", "").lower() != "submit"
        }
    )
    summary = " ".join(s.get_text(" ", strip=True) for s in form.find_all("strong"))
    return ConfirmPage(fields=fields, summary=summary)


def extract_vc(html: str) -> str | None:
    """Return the vc token from a confirm page, or None if not found."""
    page = parse_confirm(html)
    return page.vc if page else None


def shows_my_booking(html: str, day: date, court: int) -> bool:
    """True if the 'Your Bookings' page lists a booking
    of ours for that day and court.
    """
    soup = BeautifulSoup(html, "html.parser")
    wanted_day = f"{day:%A} {format_dt(day)}"  # "Friday 16 October 2026"
    for block in soup.select("div.booked_by_me"):
        text = block.get_text(" ", strip=True)
        if wanted_day in text and re.search(rf"\bCourt {court}\b", text):
            return True
    return False


def classify_result(html: str) -> Outcome:
    """Classify a result page as SUCCESS, FAILED, or UNVERIFIED."""
    text = visible_text(html).lower()
    ok = any(p in text for p in SUCCESS_PHRASES)
    bad = any(p in text for p in FAILURE_PHRASES)
    if ok and not bad:
        return Outcome.SUCCESS
    if bad and not ok:
        return Outcome.FAILED
    return Outcome.UNVERIFIED


# ---- network client -----------------------------------------------------------


class MyCourtsClient:
    def __init__(
        self,
        base_url: str,
        session: requests.Session | None = None,
        timeout: float = 10,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.last_response: requests.Response | None = None

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _send(self, method: str, path: str, **kwargs) -> requests.Response:
        resp = self.session.request(
            method, self._url(path), timeout=self.timeout, **kwargs
        )
        resp.raise_for_status()
        if "charset" not in resp.headers.get("Content-Type", ""):
            resp.encoding = "utf-8"
        self.last_response = resp
        return resp

    def login(self, username: str, password: str) -> None:
        home = self._send("GET", "/")
        form = find_login_form(home.text, home.url)
        if form is None:
            raise LoginError("No login form found on the home page")
        data = {**form.fields, form.user_field: username, form.password_field: password}
        resp = self.session.post(form.action, data=data, timeout=self.timeout)
        resp.raise_for_status()
        self.last_response = resp
        if find_login_form(resp.text, resp.url) is not None:
            raise LoginError(
                "Still on a login form after submitting: check credentials"
            )

    def server_clock_offset(self) -> float | None:
        """Server time minus local time, in seconds (1-second resolution)."""
        header = self.last_response.headers.get("Date") if self.last_response else None
        if not header:
            return None
        return (parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds()

    def booking_grid(self, days_ahead: int) -> str:
        params = {"st1": 600, "st2": 2300, "d": days_ahead, "tabID": 0}
        return self._send("GET", "bookings.asp", params=params).text

    def open_confirm(self, ct_ids: list[str], day: date) -> str:
        data = {
            "ctID_csv": ",".join(ct_ids),
            "dt": format_dt(day),
            "book_now": "Book Now",
        }
        return self._send(
            "POST",
            "bookings_confirm.asp",
            data=data,
            headers={"Referer": self._url("bookings.asp")},
        ).text

    def commit(self, page: ConfirmPage) -> str:
        """Submit exactly the fields the confirm page's form would submit."""
        return self._send(
            "POST",
            "bookings_process_multi.asp",
            data=page.fields,
            headers={"Referer": self._url("bookings_confirm.asp")},
        ).text
