# Deploying CourtSniper to Google Cloud

Runs unattended: **Cloud Scheduler** starts a **Cloud Run job** every Wednesday at 07:13 (London
time). The job logs in, waits until 07:15:00.250 and books Sunday's court. Passwords live in
**Secret Manager**; every run's log is kept in **Cloud Logging**.

```
Cloud Scheduler (Wed 07:13 Europe/London) -> Cloud Run job (europe-west2) -> mycourts + Twilio
                       Secret Manager (club password, Twilio token) -> job env vars
```

## 1. Get a terminal

**Option A: Cloud Shell (nothing to install)**
1. Open <https://console.cloud.google.com>, select your project, click the `>_` icon (top right).
2. Click the three-dot menu in the Cloud Shell bar > **Upload** > choose `courtsniper.zip`,
   then upload your `.env` the same way (uploads land in your home folder).
3. `unzip courtsniper.zip && mv ~/.env courtsniper/ && cd courtsniper`

gcloud is already installed and signed in. Cloud Shell is free. Its home folder is private to
you but persistent, so delete `.env` there after deploying (`rm .env`); the values are then
only in Secret Manager and the job settings.

**Option B: your Mac**
1. Install the SDK: `brew install --cask google-cloud-sdk`
2. `gcloud auth login`
3. `cd` into the `courtsniper` folder.

Everything after this point is identical for both options.

## 2. Configure and deploy

```bash
gcloud config set project YOUR_PROJECT_ID
bash deploy/setup.sh
```

The script reads your `.env` (the same file you use locally) and never asks you to type values
into the script:

| From `.env` | Goes to |
|---|---|
| `CLUB_PASSWORD`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | Secret Manager (piped via stdin, never in command arguments or shell history) |
| `CLUB_USERNAME`, `TWILIO_FROM`, `USER_PHONE` | plain job settings (not credentials) |

Booking settings (region, job name, schedule, court/time) live in `deploy/common.sh`; none of
them are sensitive, so every script is safe to commit. The first run
builds the container (about 3-5 minutes) and creates the schedule.

## 3. Test without booking

```bash
bash deploy/ops.sh probe 1 08:00 3      # court, first slot, number of 30-min slots [, days ahead]
```

It runs the job once and prints that run's log. Look for `confirm page OK: ... | vc=...`.
That proves the club site accepts logins from Google Cloud. Probe never books.

## 4. Set a budget alert (once)

Console > **Billing** > **Budgets & alerts** > **Create budget**: amount £1, alerts at 50% and
100%. A budget alerts by email; it does not stop spending.

## Day-to-day: `deploy/ops.sh`

| Task | Command |
|---|---|
| Test a booking, no booking made | `bash deploy/ops.sh probe 8 14:00 2 4` |
| Book now (one-off) | `bash deploy/ops.sh book 8 14:00 2 4` |
| Did it run, and how did it end? | `bash deploy/ops.sh status` |
| What happened, in detail (latest run) | `bash deploy/ops.sh logs` |
| Warnings/errors, last 7 days | `bash deploy/ops.sh errors` |
| Next scheduled run | `bash deploy/ops.sh next` |
| Pause / resume the weekly booking | `bash deploy/ops.sh pause` / `resume` |
| Fire the weekly booking now | `bash deploy/ops.sh run-now` |
| Job settings (image, args, env; no secret values) | `bash deploy/ops.sh config` |
| Deploy a code change | `bash deploy/setup.sh` |
| Change password / token | edit `.env`, re-run `bash deploy/setup.sh` |

Arguments: court, first slot, number of 30-min slots, days ahead (default 4). Logs can take a few
seconds to appear; if `logs` looks empty right after a run, run it again.

Schedule rule of thumb: the booking day minus 4 days, at 07:13. Saturday -> `13 7 * * 2`,
Sunday -> `13 7 * * 3` (0 = Sunday ... 6 = Saturday). Change it in `deploy/common.sh`, then re-run
`setup.sh`. Cloud Scheduler's free tier covers 3 jobs per billing account.

## Permissions (least privilege)

| Service account | Can do | Scope |
|---|---|---|
| `courtsniper-builder` | write build logs; read uploaded source; push images | project logs; the build bucket; the `courtsniper` repo |
| `courtsniper-runner` | read secrets | the 3 CourtSniper secrets only |
| `courtsniper-scheduler` | start the job (`run.invoker`) | this one job only |

The default compute service account is not used. `setup.sh` removes `roles/editor` and
`roles/run.builder` from it. Your own user account keeps Owner, which you need to run the scripts.

## Teardown ("eviction")

```bash
bash deploy/teardown.sh                    # delete every CourtSniper resource
bash deploy/teardown.sh --delete-project   # ...and shut down the whole project
```

Asks you to type the project ID first. Deletes the scheduler, job, secrets, images, build buckets,
service accounts and their permissions, and is safe to re-run if interrupted. With
`--delete-project` the project is shut down (billing stops; restorable for 30 days with
`gcloud projects undelete`). The Twilio number is outside GCP: release it in the Twilio console.

## Troubleshooting

- **Build fails with a permission error**: check `courtsniper-builder` has `roles/logging.logWriter`
  on the project, `roles/storage.objectViewer` on the build bucket and `roles/artifactregistry.writer`
  on the repo (`setup.sh` grants all three). A failed build leaves the deployed job unchanged.
- **Job fails with exit code 2**: configuration problem (missing env var or secret). The log says which.
- **Exit code 1**: the booking did not go through. You get the FAILED SMS; the log has the reason.
