# CourtSniper

Books a court on Brentham Club the moment it opens: 4 days ahead, at 07:15:00.
Uses plain HTTP requests (no browser) and sends an SMS through Twilio.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate     # Python 3.11+
pip install -r requirements-dev.txt   # app + pytest/ruff
cp .env.example .env              # club login + Twilio
cp config.toml.example config.toml   # court, time, number of slots
```

## Use

```bash
python -m courtsniper probe        # logs in, saves pages, NEVER books
python -m courtsniper book         # logs in, waits for 07:15:00.250, books, texts you
```

Override the target for a single run:

```bash
python -m courtsniper probe --court 2 --start 15:00 --slots 2 --days 3
```

Each run saves the pages it fetched to `runs/<timestamp>/`. `book` waits if started before
07:15; after 07:15 it books immediately.

Exit codes: 0 booked, 1 not booked, 2 config error, 3 error during the run.

## Run it in the cloud

See [deploy/DEPLOY.md](deploy/DEPLOY.md): Cloud Run job + Cloud Scheduler, about $0/month on GCP.
Planned work is in [ROADMAP.md](ROADMAP.md).

## Layout

| Module | Job |
|---|---|
| `config.py` | Loads `.env` (secrets) and `config.toml` (preferences) |
| `slots.py` | Court + time -> ctIDs, from `data/court_ctids.csv` |
| `client.py` | HTTP calls to the club site; HTML parsing helpers |
| `scheduler.py` | Target date, release moment, precise wait |
| `notify.py` | Twilio SMS (logs only if Twilio isn't configured) |
| `booker.py` | `probe` and `book` flows |
| `__main__.py` | Command line |

## Safety checks before committing

The booking is only submitted if the confirm page shows the requested court, date and slot ids
and carries a `vc` token. Requests over 1.5 hours are refused (club rule 2.1). The success SMS
reminds you to add your partner's name (rule 2.7).

## How success is detected

After booking, the site shows "Your Bookings". The run counts as SUCCESS only if that page lists a
booking of yours for the requested day and court. The SMS is plain ASCII and at most 120
characters, so it fits one segment even with Twilio's trial prefix.

## Tests

```bash
python -m pytest
```
