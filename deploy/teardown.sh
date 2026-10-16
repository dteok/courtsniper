#!/usr/bin/env bash
# Removes everything setup.sh created ("eviction" from GCP).
#
#     bash deploy/teardown.sh                    # delete all CourtSniper resources
#     bash deploy/teardown.sh --delete-project   # ...then shut down the whole project
#
# Order: stop the trigger first so nothing can run mid-teardown, then the job, data,
# permissions and identities. Every step tolerates "already gone", so it is safe to
# re-run after a partial failure.
set -uo pipefail
# shellcheck source=deploy/common.sh
source "$(dirname "$0")/common.sh"

DELETE_PROJECT=0
[[ "${1:-}" == "--delete-project" ]] && DELETE_PROJECT=1

cat <<EOF
This permanently deletes from project '$PROJECT_ID':
  - scheduler $TRIGGER, Cloud Run job $JOB (and its run history)
  - secrets: ${SECRETS[*]}
  - container repository $REPO and its images
  - buckets gs://$BUILD_BUCKET and gs://${PROJECT_ID}_cloudbuild (if present)
  - service accounts courtsniper-runner, -scheduler, -builder and their permissions
EOF
(( DELETE_PROJECT )) && echo "  - THEN THE WHOLE PROJECT (recoverable for 30 days, billing stops)"
read -r -p "Type the project ID to confirm: " answer
[[ "$answer" == "$PROJECT_ID" ]] || { echo "Cancelled."; exit 1; }

try() {  # run a delete; report but keep going if the resource is already gone
  local what="$1"; shift
  if "$@" --quiet >/dev/null 2>&1; then echo "deleted  $what"; else echo "skipped  $what (absent or already removed)"; fi
}

step "Scheduler and job"
try "scheduler $TRIGGER" gcloud scheduler jobs delete "$TRIGGER" --location "$REGION"
try "job $JOB"           gcloud run jobs delete "$JOB" --region "$REGION"

step "Secrets"
for s in "${SECRETS[@]}"; do try "secret $s" gcloud secrets delete "$s"; done

step "Images and build files"
try "repository $REPO" gcloud artifacts repositories delete "$REPO" --location "$REGION"
try "bucket gs://$BUILD_BUCKET" gcloud storage rm --recursive "gs://$BUILD_BUCKET"
try "bucket gs://${PROJECT_ID}_cloudbuild" gcloud storage rm --recursive "gs://${PROJECT_ID}_cloudbuild"

step "Project-level permissions"
try "logWriter for courtsniper-builder" gcloud projects remove-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:$BUILDER_SA" --role roles/logging.logWriter --condition=None

step "Service accounts"
for sa in "$RUNNER_SA" "$SCHEDULER_SA" "$BUILDER_SA"; do
  try "${sa%%@*}" gcloud iam service-accounts delete "$sa"
done

if (( DELETE_PROJECT )); then
  step "Shutting down project $PROJECT_ID"
  gcloud projects delete "$PROJECT_ID" --quiet && \
    echo "Project scheduled for deletion. Restore within 30 days: gcloud projects undelete $PROJECT_ID"
else
  cat <<EOF

Done. Left in place (no ongoing cost): the enabled APIs, Cloud Logging history
(expires automatically), and the project itself with its billing link.
To remove those too:  bash deploy/teardown.sh --delete-project
Not GCP: release the Twilio number in the Twilio console if you no longer need it.
EOF
fi
