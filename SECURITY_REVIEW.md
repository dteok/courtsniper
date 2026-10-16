# CourtSniper Security & Code Review (Pen-Test Style)

Scope: `courtsniper/` Python package, `tests/`, `deploy/` scripts, Dockerfile, CI.
Date: 2026-10-17.

## Findings and Remediations

### HIGH — Regex injection / false-positive court matching (`client.py`)

`ConfirmPage.shows_court()` and `shows_my_booking()` interpolated a CLI-supplied
`court` integer directly into a regex (`rf"\bCourt {court}\b"`). The value is
validated as `int` today, but any future string source (config drift, API input)
would allow ReDoS or pattern injection, and the `\b` boundary still mis-matches
inside longer sentences.

**Fix:** court numbers are parsed with an explicit, anchored digit pattern
(`_COURT_REF_RE`) and compared as integers against visible page text only.
No user data ever reaches a compiled regex.

### MEDIUM — Credentials could leak into run artifacts and logs

`find_login_form()` returned *all* non-submit form fields, including password
fields, and `login()` merged them into the POST body. A malicious/compromised
page could plant extra inputs that get echoed back; login-form HTML is saved by
the recorder and hidden values end up in debug output elsewhere.

**Fix:**
- `LoginForm` now exposes exactly `username_field` / `password_field`; all other
  fields are dropped except a small allowlist of CSRF-style hidden inputs
  (`token`, `__RequestVerificationToken`, `csrf*`, `nonce`).
- Password-field values are never read from the page (only its *name*).
- `RunRecorder.save()` refuses to write anything containing a password field and
  truncates file names (path traversal via crafted names is impossible).
- The confirm-page `vc` token is logged truncated (`vc[:8]…`) so full anti-CSRF
  tokens don't linger in Cloud Logging.

### MEDIUM — Unpinned dependencies / no integrity verification

`requirements*.txt` used lower bounds only; a compromised upstream release would
be installed silently in CI and the container.

**Fix:** all direct dependencies pinned to exact versions with `sha256` hashes
(`pip install --require-hashes` compatible), plus `pip-tools` workflow notes.

### MEDIUM — SSRF-style URL trust on the login action

`find_login_form()` joined the form `action` against the page URL without
checking the scheme/host, so a tampered page could redirect credentials to an
attacker host (`http://evil/...` or protocol-relative URLs).

**Fix:** `_safe_action()` rejects any action whose origin differs from the
configured club origin, and rejects non-HTTPS schemes; otherwise falls back to
the page URL.

### LOW — SMS content injected from page-derived detail text

`BookingReport.sms()` included `detail` (partly derived from remote page text)
and could exceed one segment. **Fix:** SMS is built only from local constants +
validated fields, ASCII-scrubbed, hard-capped at 120 chars, newline/control
characters stripped.

### LOW — Twilio imported eagerly despite being optional

A missing/broken Twilio install crashed every run even when notifications were
log-only. **Fix:** lazy import inside `TwilioNotifier.__init__`.

### LOW — Broad exception handling in `__main__`

`except Exception` swallowed everything after logging; error strings (which can
contain URLs/credentials from requests exceptions) were sent verbatim over SMS.
**Fix:** errors are mapped to short, scrubbed summaries before leaving the
process.

### Deploy scripts (reviewed, hardened where cheap)

- `setup.sh` writes secrets via stdin (no argv leakage) — good, kept.
- Added `set -euo pipefail` audit, quoted expansions verified.
- `.github/workflows/ci.yml`: added `pip-audit` job for known CVEs in pinned deps.

## Performance / Correctness Improvements

- `SlotTable` precomputes minute offsets once per slot instead of re-parsing
  `"HH:MM"` strings on every comparison (`O(n log n)` build, `O(1)` per lookup
  of consecutive runs).
- `visible_text()` result reused instead of re-parsing HTML multiple times in
  `classify_result` paths.
- Spin-wait in `scheduler.wait_until` reduced from 2 ms polling to adaptive
  10 ms → 1 ms backoff (lower CPU burn during the long wait, same accuracy).
- BeautifulSoup parser switched to `html.parser` everywhere already; parsing is
  done once per page and passed around as objects rather than re-parsed strings.

## Legibility / PEP8 / Zen of Python

- Every public module/class/function now has a Google-style docstring
  (`Args:` / `Returns:` / `Raises:`).
- Removed commented-out test code; tests given docstrings; pylint disables for
  missing docstrings removed from `pyproject.toml` (enforced going forward via
  ruff `D` rules + pydocstyle convention=google).
- Names made consistent (`user_field` → `username_field`), magic numbers pulled
  into named constants, "Explicit is better than implicit" applied to outcome
  classification.
- Ruff lint config extended: `select = ["E","F","W","I","B","UP","D","S","SIM","RET"]`
  with `D` using google convention (tests exempt from some D rules).
