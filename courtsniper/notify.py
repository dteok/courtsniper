"""SMS notifications via Twilio, with a log-only fallback."""

from __future__ import annotations
import logging

from typing import Protocol
from twilio.rest import Client  # imported lazily: optional dependency

from .config import Settings

log = logging.getLogger(__name__)


class Notifier(Protocol):
    def send(self, text: str) -> None: ...


class LogNotifier:
    """Used when Twilio isn't configured: the message only goes to the log."""

    def send(self, text: str) -> None:
        log.info("SMS (not sent, Twilio not configured): %s", text)


class TwilioNotifier:
    def __init__(self, sid: str, token: str, from_: str, to: str):
        self._client = Client(sid, token)
        self._from, self._to = from_, to

    def send(self, text: str) -> None:
        try:
            msg = self._client.messages.create(to=self._to, from_=self._from, body=text)
            log.info("SMS sent to %s (SID %s)", self._to, msg.sid)
        # an SMS failure must never mask the booking result
        except Exception:  # pylint: disable=broad-exception-caught
            log.exception("SMS failed")


def build_notifier(settings: Settings) -> Notifier:
    creds = (
        settings.twilio_sid,
        settings.twilio_token,
        settings.twilio_from,
        settings.notify_to,
    )
    if all(creds):
        return TwilioNotifier(*creds)
    return LogNotifier()
