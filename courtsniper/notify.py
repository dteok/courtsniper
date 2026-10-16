"""SMS notifications via Twilio, with a log-only fallback.

Twilio is an optional dependency: it is imported lazily so runs without it
still work through :class:`LogNotifier`.
"""

from __future__ import annotations

import logging
import re
from typing import Protocol

from .config import Settings

log = logging.getLogger(__name__)

# E.164 phone numbers and Twilio account sids / from numbers.
_E164_RE = re.compile(r"^\+\d{6,15}$")
_SID_RE = re.compile(r"^AC[0-9a-fA-F]{32}$")
_FROM_RE = re.compile(r"^(?:\+\d{6,15}|MG[0-9a-fA-F]{32}|AP[0-9a-fA-F]{32})$")


class Notifier(Protocol):
    """Anything that can deliver one notification message."""

    def send(self, text: str) -> None:
        """Deliver a short message.

        Args:
            text: The message body.
        """
        ...


class LogNotifier:
    """Used when Twilio isn't configured: the message only goes to the log."""

    def send(self, text: str) -> None:
        """Log the message instead of sending it.

        Args:
            text: The message body.
        """
        log.info("SMS (not sent, Twilio not configured): %s", text)


class TwilioNotifier:
    """Sends SMS through Twilio; failures are logged, never raised."""

    def __init__(self, sid: str, token: str, from_: str, to: str) -> None:
        """Create a notifier bound to one Twilio account and destination.

        Args:
            sid: Twilio account SID (``AC...``).
            token: Twilio auth token.
            from_: Twilio from number or messaging service SID.
            to: Destination phone number in E.164 format.

        Raises:
            ValueError: If any credential has an unexpected shape (guards
                against config mix-ups before hitting the API).
        """
        if not _SID_RE.match(sid):
            raise ValueError("TWILIO_ACCOUNT_SID does not look like an AC... SID")
        if not _FROM_RE.match(from_):
            raise ValueError("TWILIO_FROM is neither an E.164 number nor a MG/AP SID")
        if not _E164_RE.match(to):
            raise ValueError("USER_PHONE must be an E.164 number, e.g. +4479...")
        if not token or len(token) > 64:
            raise ValueError("TWILIO_AUTH_TOKEN looks invalid")

        from twilio.rest import Client  # lazy: optional dependency

        self._client = Client(sid, token)
        self._from, self._to = from_, to

    def send(self, text: str) -> None:
        """Send one SMS; an SMS failure must never mask the booking result.

        Args:
            text: The message body.
        """
        try:
            msg = self._client.messages.create(to=self._to, from_=self._from, body=text)
            log.info("SMS sent (SID %s)", msg.sid)
        except Exception:  # pylint: disable=broad-exception-caught
            log.exception("SMS failed")


def build_notifier(settings: Settings) -> Notifier:
    """Return a TwilioNotifier if all credentials are set, else a LogNotifier.

    Args:
        settings: Loaded configuration.

    Returns:
        Notifier: Ready-to-use notifier.
    """
    creds = (
        settings.twilio_sid,
        settings.twilio_token,
        settings.twilio_from,
        settings.notify_to,
    )
    if all(creds):
        try:
            return TwilioNotifier(*creds)
        except ValueError as exc:
            log.error("Twilio credentials unusable (%s); falling back to log-only", exc)
    return LogNotifier()
