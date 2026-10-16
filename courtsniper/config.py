"""Settings: secrets from .env, booking preferences from config.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, replace
from datetime import time
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Target:
    """The slots to book: a court, a first slot time, and a number of 30-min slots."""

    court: int
    start: str
    slots: int


@dataclass(frozen=True)
class Settings:  # pylint: disable=too-many-instance-attributes
    base_url: str
    timezone: str
    username: str
    password: str
    target: Target
    lead_days: int
    release_time: time
    fire_delay_ms: int
    twilio_sid: str | None
    twilio_token: str | None
    twilio_from: str | None
    notify_to: str | None
    notify_on_failure: bool

    def with_target(self, **changes) -> Settings:
        """Return a copy with some target fields overridden (from the CLI)."""
        changes = {k: v for k, v in changes.items() if v is not None}
        return replace(self, target=replace(self.target, **changes))


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing {name} in .env")
    return value


def load_settings(config_path: Path, env_path: Path | None = None) -> Settings:
    """Load settings. Secrets come only from the environment / .env."""
    load_dotenv(env_path)
    try:
        with open(config_path, "rb") as f:
            cfg = tomllib.load(f)
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {config_path}") from exc

    try:
        club, booking, target = cfg["club"], cfg["booking"], cfg["target"]
        notify = cfg.get("notify", {})
        return Settings(
            base_url=club["base_url"].rstrip("/"),
            timezone=club.get("timezone", "Europe/London"),
            username=_require_env("CLUB_USERNAME"),
            password=_require_env("CLUB_PASSWORD"),
            target=Target(
                court=int(target["court"]),
                start=str(target["start"]),
                slots=int(target.get("slots", 2)),
            ),
            lead_days=int(booking.get("lead_days", 4)),
            release_time=time.fromisoformat(booking.get("release_time", "07:15:00")),
            fire_delay_ms=int(booking.get("fire_delay_ms", 250)),
            twilio_sid=os.getenv("TWILIO_ACCOUNT_SID") or None,
            twilio_token=os.getenv("TWILIO_AUTH_TOKEN") or None,
            twilio_from=os.getenv("TWILIO_FROM") or None,
            notify_to=os.getenv("USER_PHONE") or None,
            notify_on_failure=bool(notify.get("on_failure", True)),
        )
    except KeyError as exc:
        raise ConfigError(f"Missing key in {config_path}: {exc}") from exc
    except ValueError as exc:
        raise ConfigError(f"Invalid value in {config_path}: {exc}") from exc
