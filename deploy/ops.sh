#!/usr/bin/env bash
# Day-to-day commands for the deployed CourtSniper job.
#     bash deploy/ops.sh help
set -euo pipefail
# shellcheck source=deploy/common.sh
source "$(dirname "$0")/common.sh"

LOG_FILTER="resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"$JOB\""

usage() {
  cat <<EOF
Usage: bash deploy/ops.sh <command> [args]

  probe COURT START SLOTS [DAYS]  Test a booking now without booking (e.g. probe 8 14:00 2 4)
  book  COURT START SLOTS [DAYS]  Book now, one-off (DAYS ahead, default 4); sends the SMS
  status [N]                      Last N runs and how they ended (default 5)
  logs                            Full log of the most recent run, oldest line first
  errors [DAYS]                   Warnings and errors from the last DAYS days (default 7)
  next                            When the scheduled booking runs next
  run-now                         Fire the scheduled booking immediately (uses its own args)
  pause | resume                  Stop / restart the weekly schedule
  config                          Show the job's image, args and settings (no secret values)

Scheduled booking: $BOOK_ARGS, cron '$SCHEDULE' ($TIME_ZONE)
EOF
}

execute() {  # $1 = probe|book, then COURT START SLOTS [DAYS]
  local mode="$1"; shift
  [[ $# -ge 3 ]] || { usage; exit 1; }
  local args="$mode,--court,$1,--start,$2,--slots,$3,--days,${4:-4}"
  echo "Running: $args"
  gcloud run jobs execute "$JOB" --region "$REGION" --args "$args" --wait || true
  show_logs
}

show_logs() {
  local exec_name
  exec_name="$(gcloud run jobs executions list --job "$JOB" --region "$REGION" \
    --sort-by '~metadata.creationTimestamp' --limit 1 --format 'value(metadata.name)')"
  [[ -n "$exec_name" ]] || { echo "No runs yet."; return; }
  echo "--- log of $exec_name ---"
  gcloud logging read "$LOG_FILTER AND labels.\"run.googleapis.com/execution_name\"=\"$exec_name\"" \
    --order asc --freshness 7d --limit 200 --format 'value(textPayload)'
}

case "${1:-help}" in
  probe) shift; execute probe "$@" ;;
  book)  shift; execute book "$@" ;;
  status)
    gcloud run jobs executions list --job "$JOB" --region "$REGION" \
      --sort-by '~metadata.creationTimestamp' --limit "${2:-5}"
    echo "(COMPLETE 1/1 = booked; 0/1 = not booked or error. Details: bash deploy/ops.sh logs)" ;;
  logs)    show_logs ;;
  errors)
    # App lines are plain text (severity unset), so match their level text as well.
    gcloud logging read "$LOG_FILTER AND (severity>=WARNING OR textPayload=~\" (WARNING|ERROR) \")" \
      --freshness "${2:-7}d" \
      --order asc --limit 100 --format 'value(timestamp.date(tz=Europe/London), textPayload)' ;;
  next)
    gcloud scheduler jobs describe "$TRIGGER" --location "$REGION" \
      --format 'value(state, scheduleTime.date(tz=Europe/London))' ;;
  run-now) gcloud scheduler jobs run "$TRIGGER" --location "$REGION" && echo "Started; check: bash deploy/ops.sh status" ;;
  pause)   gcloud scheduler jobs pause "$TRIGGER" --location "$REGION" ;;
  resume)  gcloud scheduler jobs resume "$TRIGGER" --location "$REGION" ;;
  config)
    gcloud run jobs describe "$JOB" --region "$REGION" ;;
  help|-h|--help) usage ;;
  *) usage; exit 1 ;;
esac
