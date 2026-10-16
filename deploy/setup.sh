#!/usr/bin/env bash
# CourtSniper on GCP: Cloud Run job + Cloud Scheduler + Secret Manager.
#
# Run from the project root (the folder containing Dockerfile):
#     bash deploy/setup.sh
#
# Booking settings live in deploy/common.sh. Credentials are read
# from .env (gitignored): credentials go to Secret Manager, the rest become plain job
# settings. Use another file with: ENV_FILE=path/to/file bash deploy/setup.sh
#
# Least privilege: three dedicated service accounts, each with only what it needs.
#   courtsniper-builder    builds the image  (log writer; read build bucket; push to repo)
#   courtsniper-runner     runs the job      (read the 3 secrets only)
#   courtsniper-scheduler  starts the job    (run.invoker on this job only)
# The default compute service account is not used, and its broad roles are removed.
#
# Safe to re-run: existing resources are reused and updated.

set -euo pipefail
[[ -f Dockerfile ]] || { echo "Run this from the project root (where Dockerfile is)."; exit 1; }
# shellcheck source=deploy/common.sh
source "$(dirname "$0")/common.sh"

ENV_FILE="${ENV_FILE:-.env}"
[[ -f "$ENV_FILE" ]] || { echo "No $ENV_FILE found. Copy .env.example to .env and fill it in."; exit 1; }

GDOMAIN=".googleapis.com"

env_get() {  # read KEY from the env file without executing it (no `source`)
  local line
  line="$(grep -E "^[[:space:]]*$1[[:space:]]*=" "$ENV_FILE" | tail -n 1 || true)"
  line="${line#*=}"
  line="${line#"${line%%[![:space:]]*}"}"; line="${line%"${line##*[![:space:]]}"}"
  line="${line%\"}"; line="${line#\"}"; line="${line%\'}"; line="${line#\'}"
  printf '%s' "$line"
}

for key in CLUB_USERNAME CLUB_PASSWORD TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN TWILIO_FROM USER_PHONE; do
  [[ -n "$(env_get "$key")" ]] || { echo "$key is missing or empty in $ENV_FILE"; exit 1; }
done

step "Project: $PROJECT_ID, region: $REGION"
gcloud config set run/region "$REGION" >/dev/null
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

step "Enabling APIs (first run takes a minute)"
gcloud services enable run$GDOMAIN cloudscheduler$GDOMAIN \
  secretmanager$GDOMAIN cloudbuild$GDOMAIN artifactregistry$GDOMAIN storage$GDOMAIN

step "Service accounts"
for sa in courtsniper-runner courtsniper-scheduler courtsniper-builder; do
  if ! gcloud iam service-accounts describe "${sa}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$sa" --display-name "CourtSniper ${sa#courtsniper-}"
  fi
done

has_project_role() {  # $1 = member, $2 = role
  [[ -n "$(gcloud projects get-iam-policy "$PROJECT_ID" --flatten='bindings[].members' \
    --filter="bindings.members=\"$1\" AND bindings.role=\"$2\"" --format='value(bindings.role)')" ]]
}

step "Least privilege: tightening the default compute service account"
COMPUTE_SA="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
for role in roles/run.builder roles/editor; do
  if has_project_role "$COMPUTE_SA" "$role"; then
    gcloud projects remove-iam-policy-binding "$PROJECT_ID" --member "$COMPUTE_SA" \
      --role "$role" --condition=None >/dev/null
    echo "removed $role from ${COMPUTE_SA#serviceAccount:}"
  else
    echo "${COMPUTE_SA#serviceAccount:}: no $role"
  fi
done

sync_secret() {  # $1 = secret name, $2 = key in the env file
  local name="$1" value latest old
  value="$(env_get "$2")"
  if ! gcloud secrets describe "$name" >/dev/null 2>&1; then
    gcloud secrets create "$name" --replication-policy=automatic >/dev/null
  fi
  latest="$(gcloud secrets versions access latest --secret "$name" 2>/dev/null || true)"
  if [[ "$value" == "$latest" ]]; then
    echo "$name: unchanged"
  else
    # Piped through stdin: the value never appears in arguments or shell history.
    printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- >/dev/null
    # Keep one active version per secret (the free tier covers 6 in total).
    for old in $(gcloud secrets versions list "$name" --filter='state=ENABLED' \
                   --sort-by='~createTime' --format='value(name)' | tail -n +2); do
      gcloud secrets versions destroy "$old" --secret "$name" --quiet >/dev/null
    done
    echo "$name: updated"
  fi
  gcloud secrets add-iam-policy-binding "$name" --member "serviceAccount:$RUNNER_SA" \
    --role roles/secretmanager.secretAccessor >/dev/null
}

step "Secrets from $ENV_FILE -> Secret Manager (readable by the runner only)"
sync_secret "${SECRETS[0]}" CLUB_PASSWORD
sync_secret "${SECRETS[1]}" TWILIO_ACCOUNT_SID
sync_secret "${SECRETS[2]}" TWILIO_AUTH_TOKEN

step "Build bucket gs://$BUILD_BUCKET (source uploads, auto-deleted after 7 days)"
if ! gcloud storage buckets describe "gs://$BUILD_BUCKET" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$BUILD_BUCKET" --location "$REGION" \
    --uniform-bucket-level-access --public-access-prevention
fi
gcloud storage buckets update "gs://$BUILD_BUCKET" \
  --lifecycle-file deploy/build-bucket-lifecycle.json >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://$BUILD_BUCKET" \
  --member "serviceAccount:$BUILDER_SA" --role roles/storage.objectViewer >/dev/null

step "Container repository ($REPO in $REGION)"
if ! gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPO" --repository-format=docker \
    --location "$REGION" --description "CourtSniper container images"
fi
# Keep only the 2 newest images, so storage stays well inside the 0.5 GB free tier.
gcloud artifacts repositories set-cleanup-policies "$REPO" --location "$REGION" \
  --policy deploy/cleanup-policy.json --no-dry-run >/dev/null
gcloud artifacts repositories add-iam-policy-binding "$REPO" --location "$REGION" \
  --member "serviceAccount:$BUILDER_SA" --role roles/artifactregistry.writer >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:$BUILDER_SA" --role roles/logging.logWriter --condition=None >/dev/null

step "Building the container as courtsniper-builder (a few minutes)"
gcloud builds submit . \
  --region "$REGION" \
  --config deploy/cloudbuild.yaml \
  --substitutions "_IMAGE=$IMAGE" \
  --service-account "projects/$PROJECT_ID/serviceAccounts/$BUILDER_SA" \
  --gcs-source-staging-dir "gs://$BUILD_BUCKET/source"

step "Deploying job $JOB"
gcloud run jobs deploy "$JOB" \
  --image "$IMAGE" \
  --region "$REGION" \
  --service-account "$RUNNER_SA" \
  --set-env-vars "CLUB_USERNAME=$(env_get CLUB_USERNAME),TWILIO_FROM=$(env_get TWILIO_FROM),USER_PHONE=$(env_get USER_PHONE)" \
  --set-secrets "CLUB_PASSWORD=${SECRETS[0]}:latest,TWILIO_ACCOUNT_SID=${SECRETS[1]}:latest,TWILIO_AUTH_TOKEN=${SECRETS[2]}:latest" \
  --args "$BOOK_ARGS" \
  --max-retries 0 \
  --task-timeout 600s \
  --cpu 1 --memory 512Mi

step "Allowing the scheduler to start this job (and nothing else)"
gcloud run jobs add-iam-policy-binding "$JOB" --region "$REGION" \
  --member "serviceAccount:$SCHEDULER_SA" --role roles/run.invoker >/dev/null

step "Scheduling: '$SCHEDULE' ($TIME_ZONE)"
RUN_URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run"
if gcloud scheduler jobs describe "$TRIGGER" --location "$REGION" >/dev/null 2>&1; then
  action=update
else
  action=create
fi
gcloud scheduler jobs "$action" http "$TRIGGER" \
  --location "$REGION" \
  --schedule "$SCHEDULE" \
  --time-zone "$TIME_ZONE" \
  --uri "$RUN_URI" \
  --http-method POST \
  --oauth-service-account-email "$SCHEDULER_SA" >/dev/null

cat <<EOF

Done. Next run: $(gcloud scheduler jobs describe "$TRIGGER" --location "$REGION" --format 'value(scheduleTime)') (UTC)
Day-to-day commands:  bash deploy/ops.sh help
EOF