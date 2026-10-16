# Shared settings for setup.sh, ops.sh and teardown.sh. Sourced, not run.
# Nothing sensitive here: secrets live in .env only.
# shellcheck shell=bash disable=SC2034

# ---- booking settings ------------------------------------------------------------
REGION="europe-west2"                         # London
JOB="courtsniper-sun-clay1-0800"              # one Cloud Run job per weekly booking
SCHEDULE="13 7 * * 3"                         # Wednesdays 07:13 -> books Sunday (4 days ahead)
TIME_ZONE="Europe/London"
BOOK_ARGS="book,--court,1,--start,08:00,--slots,3"
# ----------------------------------------------------------------------------------

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
[[ -n "$PROJECT_ID" ]] || { echo "Set a project: gcloud config set project <PROJECT_ID>"; exit 1; }

TRIGGER="${JOB}-trigger"
RUNNER_SA="courtsniper-runner@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULER_SA="courtsniper-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"
BUILDER_SA="courtsniper-builder@${PROJECT_ID}.iam.gserviceaccount.com"
REPO="courtsniper"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/courtsniper:latest"
BUILD_BUCKET="${PROJECT_ID}-courtsniper-build"
SECRETS=(courtsniper-club-password courtsniper-twilio-sid courtsniper-twilio-token)

step() { printf '\n==> %s\n' "$*"; }
