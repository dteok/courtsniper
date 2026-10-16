# Roadmap

## v1 (current)
- [x] Requests-based booking (no browser), confirm-page checks, club rules 2.1 / 2.7
- [x] Twilio SMS (one segment, plain ASCII)
- [x] Cloud Run job + Cloud Scheduler + Secret Manager; logs in Cloud Logging
- [x] Least-privilege service accounts; ops helper; full teardown script

## Next
- [ ] **Fallback list**: try alternatives in order (another court, then a later time) when the
      first choice is taken. Two HTTP requests per attempt, so a whole list takes about a second.
- [ ] **Free notifications**: replace or complement Twilio with a free channel (email, or a
      push-notification app such as ntfy or Telegram) behind the existing `Notifier` protocol.
- [ ] **Slot ID check against the live grid**: confirm the table's ctIDs against `bookings.asp`
      before booking, so a schedule change at the club can't silently break the table.
- [ ] **Report the club's own error message**: when the confirm page is an error page, read the
      red message (`<div class="wrap">`) and put it in the log and SMS instead of the generic
      "slot taken or not bookable". Seen so far: "Maximum 1.5 hours per member per day when
      booking 1 day or more in advance..." and "court already booked". Real pages saved as
      `tests/fixtures/error_daily_limit.html` and `error_court_booked.html` (name redacted).
- [ ] **Retry until bookings open**: if the confirm page says the slots aren't open yet, retry
      every 250 ms for up to ~10 s, so a club clock running slightly behind can't cause a miss.
- [ ] **Keep run pages**: upload `runs/` to Cloud Storage, only if it can be kept at zero cost
      (free tier storage is US-region only; a London bucket would cost fractions of a penny).
- [ ] **CI**: run `pytest` and `ruff` on every push (GitHub Actions; free for this volume).
- [ ] **Several bookings from one config file** instead of one deploy per booking.
