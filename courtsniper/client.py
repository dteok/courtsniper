"""HTTP client for a mycourts.co.uk club site, plus pure HTML-parsing helpers.

Booking is two POSTs, mirroring what the browser does:
  1. bookings_confirm.asp       -> "slide to confirm" page, which carries a vc token
  2. bookings_process_multi.asp -> commits the booking (vc, dt, ctid_csv)
"""

from __future__ import annotations

import enum
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

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
    """Raised when the site's login flow cannot be completed."""


class UnsafeUrlError(LoginError):
    """Raised when a page points the client at an off-origin or insecure URL."""


class Outcome(enum.Enum):
    """Result classification of a booking attempt."""

    SUCCESS = "success"
    FAILED = "failed"
    UNVERIFIED = "unverified"  # page matched neither list: check the club site


@dataclass(frozen=True)
class LoginForm:
    """The credentials form found on a page, with only safe fields kept.

    Attributes:
        action: Absolute URL to POST the credentials to (same-origin, https).
        hidden_fields: Allow-listed CSRF-style hidden inputs to echo back.
        username_field: Name of the text/email input.
        password_field: Name of the password input (its value is never read).
    """

    action: str
    hidden_fields: dict[str, str] = field(default_factory=dict)
    username_field: str = ""
    password_field: str = ""


# Hidden inputs we are willing to echo back to the same origin. Anything else
# in the form is dropped so a planted field cannot smuggle data through us.
_ALLOWED_HIDDEN = re.compile(
    r"^(token|nonce|csrf|xsrf|.*request.?verification.?token)$", re.I
)
_INSECURE_SCHEMES = ("http", "ws", "wss", "ftp")


def _origin(url: str) -> tuple[str, str, int | None]:
    """Return the (scheme, host, port) origin triple of a URL.

    Args:
        url: An absolute URL.

    Returns:
        Tuple of lowercase scheme, lowercase hostname and port (or None).
    """
    parts = urlparse(url)
    try:
        port = parts.port
    except ValueError:  # malformed port, e.g. "host:abc"
        port = None
    return parts.scheme.lower(), parts.hostname or "", port


def _same_origin(a: str, b: str) -> bool:
    """True if two URLs share scheme, host and port.

    Args:
        a: First absolute URL.
        b: Second absolute URL.

    Returns:
        bool: Whether both URLs have the same origin.
    """
    return _origin(a) == _origin(b)


def _safe_action(raw_action: str | None, page_url: str) -> str:
    """Resolve a form action against the page URL and pin it to the club origin.

    Guards against a tampered or malicious page posting credentials elsewhere
    (SSRF / credential exfiltration via off-site or downgraded actions).

    Args:
        raw_action: The form's ``action`` attribute, may be relative or missing.
        page_url: Absolute URL of the page the form was parsed from.

    Returns:
        str: Absolute https URL on the same origin as ``page_url``.

    Raises:
        UnsafeUrlError: If the action resolves off-origin or to an insecure
            scheme.
    """
    target = urljoin(page_url, raw_action or page_url)
    scheme, _, _ = _origin(target)
    if scheme in _INSECURE_SCHEMES:
        raise UnsafeUrlError(
            f"Refusing to post credentials over {scheme!r}: {target!r}"
        )
    if scheme not in ("https",) or not _same_origin(target, page_url):
        raise UnsafeUrlError(f"Login action off-origin: {target!r}")
    return target


def _scrub_fragment(url: str) -> str:
    """Drop any query/fragment from a URL before logging it.

    Args:
        url: Any URL string.

    Returns:
        str: The URL without fragment (and without query).
    """
    parts = urlparse(url)
    return urlunparse(parts._replace(query="", fragment=""))


# ---- pure helpers (tested offline) -------------------------------------------


def format_dt(day: date) -> str:
    """Format a date the way the club site displays it.

    Args:
        day: The date to format.

    Returns:
        str: e.g. ``'12 October 2026'`` (no leading zero on the day).
    """
    return f"{day.day} {day:%B %Y}"


def visible_text(html: str) -> str:
    """Return the page's text with scripts and styles removed.

    Only visible text is trusted for outcome detection; markup inside
    ``<script>``/``<style>`` never influences classification.

    Args:
        html: Raw page HTML.

    Returns:
        str: Whitespace-normalised visible text.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def find_login_form(html: str, page_url: str) -> LoginForm | None:
    """Locate the credentials form on a page and read only its safe parts.

    Security notes:
        * The password input's *value* is never read from the page — only its
          field name — so a planted value cannot be echoed back.
        * Other inputs are kept only if their names look like CSRF tokens
          (see ``_ALLOWED_HIDDEN``); everything else is dropped.
        * The resolved action must be https and same-origin as ``page_url``,
          otherwise :class:`UnsafeUrlError` is raised.

    Args:
        html: Raw page HTML.
        page_url: Absolute URL the page was fetched from.

    Returns:
        LoginForm | None: The parsed form, or None if no usable login form.

    Raises:
        UnsafeUrlError: If a login form exists but posts off-origin or over an
            insecure scheme.
    """
    soup = BeautifulSoup(html, "html.parser")
    pw = soup.find("input", attrs={"type": "password"})
    form = pw.find_parent("form") if pw else None
    if form is None:
        return None
    user = form.find("input", attrs={"type": re.compile(r"^(text|email)$", re.I)})
    if user is None or not user.get("name") or not pw.get("name"):
        return None
    hidden = {
        el["name"]: el.get("value", "")
        for el in form.find_all("input")
        if el.get("name")
        and _ALLOWED_HIDDEN.match(el["name"])
        and el.get("type", "").lower() == "hidden"
    }
    return LoginForm(
        action=_safe_action(form.get("action"), page_url),
        hidden_fields=hidden,
        username_field=user["name"],
        password_field=pw["name"],
    )


# "Court 7" style references; digits parsed explicitly, never interpolated
# from caller data into the pattern itself.
_COURT_REF_RE = re.compile(r"\bcourt\s+(\d{1,3})\b", re.I)


def _mentions_court(text: str, court: int) -> bool:
    """True if visible text mentions exactly this court number.

    Args:
        text: Page text to search.
        court: Court number to look for.

    Returns:
        bool: Whether "Court N" appears with N == court.
    """
    return any(int(found) == court for found in _COURT_REF_RE.findall(text))


@dataclass(frozen=True)
class ConfirmPage:
    """The 'Are you sure?' page: the fields its form would submit, and what it shows.

    Attributes:
        fields: Form inputs (``vc``, ``dt``, ``ctid_csv``, ...).
        summary: Visible bold text, e.g. "Tennis Court 7 Friday 16 October...".
    """

    fields: dict[str, str]  # vc, dt, ctid_csv
    summary: str  # e.g. "Tennis Court 7 Friday 16 October 2026 800 hrs, ..."

    @property
    def vc(self) -> str | None:
        """The anti-CSRF confirmation token, or None if absent."""
        return self.fields.get("vc") or None

    @property
    def ct_ids(self) -> list[str]:
        """Slot ids listed on the confirm page, in order."""
        return [c for c in self.fields.get("ctid_csv", "").split(",") if c]

    def shows_court(self, court: int) -> bool:
        """True if the confirm page names exactly this court.

        Args:
            court: Court number expected on the page.

        Returns:
            bool: Whether the summary mentions this court.
        """
        return _mentions_court(self.summary, court)


_JS_STRING = re.compile(r"""'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)\"""")
_DOC_WRITE = re.compile(r"document\.write\((.*?)\)\s*;?", re.S)


def _expand_document_write(script: str) -> str:
    """Join string literals in `document.write('<'+'inp'+...)` into the HTML it writes.

    Args:
        script: Contents of a ``<script>`` block.

    Returns:
        str: Concatenated HTML fragments written via ``document.write``.
    """
    parts = []
    for call in _DOC_WRITE.findall(script):
        parts.extend(a if a or not b else b for a, b in _JS_STRING.findall(call))
    return "".join(parts)


# Only these field names are ever sent to bookings_process_multi.asp; anything
# else planted by a page is dropped (defence against form-field smuggling).
_CONFIRM_FIELDS = ("vc", "dt", "ctid_csv")


def parse_confirm(html: str) -> ConfirmPage | None:
    """Read the booking form, including inputs the page writes with JavaScript.

    Args:
        html: Raw confirm-page HTML.

    Returns:
        ConfirmPage | None: Parsed form, or None if there is no booking form.
    """
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
    """Return the vc token from a confirm page, or None if not found.

    Args:
        html: Raw confirm-page HTML.

    Returns:
        str | None: The confirmation token, or None.
    """
    page = parse_confirm(html)
    return page.vc if page else None


def shows_my_booking(html: str, day: date, court: int) -> bool:
    """True if the 'Your Bookings' page lists our booking for that day/court.

    Args:
        html: Raw result-page HTML.
        day: Booking date being verified.
        court: Court number being verified.

    Returns:
        bool: Whether a booked-by-me block matches day and court exactly.
    """
    soup = BeautifulSoup(html, "html.parser")
    wanted_day = f"{day:%A} {format_dt(day)}"  # "Friday 16 October 2026"
    for block in soup.select("div.booked_by_me"):
        text = block.get_text(" ", strip=True)
        if wanted_day in text and _mentions_court(text, court):
            return True
    return False


def classify_result(html: str) -> Outcome:
    """Classify a result page as SUCCESS, FAILED, or UNVERIFIED.

    Matching runs on visible text only (case-insensitive); scripts and styles
    are stripped first so hidden markup cannot fake an outcome.

    Args:
        html: Raw result-page HTML.

    Returns:
        Outcome: SUCCESS when only success phrases match, FAILED when only
            failure phrases match, UNVERIFIED otherwise (or when both match —
            ambiguous pages are never trusted).
    """
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
    """Small, opinionated HTTP wrapper around one club site.

    Attributes:
        base_url: Scheme+host of the club site (https enforced).
        timeout: Per-request timeout in seconds.
        session: Underlying :class:`requests.Session` (cookies persist).
    """

    def __init__(
        self,
        base_url: str,
        session: requests.Session | None = None,
        timeout: float = 10,
    ) -> None:
        """Create a client bound to one origin.

        Args:
            base_url: Club site URL; must be https.
            session: Optional pre-configured session (for tests/proxies).
            timeout: Request timeout in seconds.

        Raises:
            UnsafeUrlError: If ``base_url`` is not a plain https URL.
        """
        scheme, host, port = _origin(base_url)
        if scheme != "https" or not host or ".." in base_url:
            raise UnsafeUrlError(f"base_url must be a plain https origin: {base_url!r}")
        del port  # origin equality is checked per-URL at use time
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.last_response: requests.Response | None = None

    def _url(self, path: str) -> str:
        """Build an absolute same-origin URL for a path.

        Args:
            path: Site-relative path (leading slash optional).

        Returns:
            str: Absolute URL under ``base_url``.
        """
        return f"{self.base_url}/{path.lstrip('/')}"

    def _send(self, method: str, path: str, **kwargs) -> requests.Response:
        """Send a request to the club origin, raising on HTTP errors.

        Args:
            method: HTTP verb.
            path: Site-relative path.
            **kwargs: Passed through to :meth:`requests.Session.request`.

        Returns:
            requests.Response: The successful response (utf-8 default encoding).

        Raises:
            requests.HTTPError: On 4xx/5xx status.
        """
        resp = self.session.request(
            method, self._url(path), timeout=self.timeout, **kwargs
        )
        resp.raise_for_status()
        if "charset" not in resp.headers.get("Content-Type", ""):
            resp.encoding = "utf-8"
        self.last_response = resp
        return resp

    def login(self, username: str, password: str) -> None:
        """Log in by submitting the credentials form found on the home page.

        Args:
            username: Club account e-mail.
            password: Club account password.

        Raises:
            LoginError: No usable form, or still on a login page after POST.
            UnsafeUrlError: The form posts off-origin or over http.
        """
        home = self._send("GET", "/")
        form = find_login_form(home.text, home.url)
        if form is None:
            raise LoginError("No login form found on the home page")
        data = {
            **form.hidden_fields,
            form.username_field: username,
            form.password_field: password,
        }
        resp = self.session.post(form.action, data=data, timeout=self.timeout)
        resp.raise_for_status()
        self.last_response = resp
        if find_login_form(resp.text, resp.url) is not None:
            raise LoginError(
                "Still on a login form after submitting: check credentials"
            )

    def server_clock_offset(self) -> float | None:
        """Server time minus local time, in seconds (1-second resolution).

        Returns:
            float | None: Offset, or None if no response with a Date header yet.
        """
        header = self.last_response.headers.get("Date") if self.last_response else None
        if not header:
            return None
        return (parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds()

    def booking_grid(self, days_ahead: int) -> str:
        """Fetch the availability grid ``days_ahead`` days out.

        Args:
            days_ahead: Number of days from today.

        Returns:
            str: Grid page HTML.
        """
        params = {"st1": 600, "st2": 2300, "d": days_ahead, "tabID": 0}
        return self._send("GET", "bookings.asp", params=params).text

    def open_confirm(self, ct_ids: Iterable[str], day: date) -> str:
        """Open the 'slide to confirm' page for the given slots and date.

        Args:
            ct_ids: Slot ids to book.
            day: Booking date.

        Returns:
            str: Confirm page HTML.
        """
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
        """Submit exactly the vetted fields the confirm page's form carries.

        Args:
            page: Parsed confirm page (must have passed ``check_confirm``).

        Returns:
            str: Result page HTML.
        """
        data = {name: page.fields[name] for name in _CONFIRM_FIELDS if name in page.fields}
        return self._send(
            "POST",
            "bookings_process_multi.asp",
            data=data,
            headers={"Referer": self._url("bookings_confirm.asp")},
        ).text
