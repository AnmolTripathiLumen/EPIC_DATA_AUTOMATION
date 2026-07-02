# Jira Epic Quality Report — GCP Cloud Run

Automated Jira epic quality scoring. Fetches epic hierarchy from Jira, scores features with Vertex AI (Gemini), and uploads results to BigQuery. Deployed as a Cloud Run service triggered on a schedule.

## Architecture

```
Scheduler (Cloud Scheduler / Power Automate / cron)
  → HTTP POST  →  Cloud Run Service (this repo)
                     ├── Jira REST API  (fetch epics/features/stories)
                     ├── Vertex AI      (LLM quality scoring)
                     └── BigQuery       (upload results)
```

## Project Structure

```
├── jira_data_extraction_enhanced.py   # Core: Jira fetch + LLM scoring + BigQuery upload
├── app.py                             # Flask HTTP wrapper for Cloud Run
├── epic_keys.txt                      # Epic keys to process (one per line)
├── requirements.txt                   # Python dependencies
├── Dockerfile                         # Container image
├── deploy.sh                          # One-command GCP deployment
├── .env.example                       # Environment variable template
└── .dockerignore                      # Docker build exclusions
```

## Deploy to GCP

### Prerequisites

- [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) installed and authenticated
- APIs enabled:
  ```bash
  gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
  ```

### 1. Store Jira API token in Secret Manager

```bash
echo -n "YOUR_JIRA_API_TOKEN" | gcloud secrets create jira-api-token --data-file=-
```

### 2. Deploy

```bash
bash deploy.sh --secret "YOUR_API_SECRET"
```

### 3. Bind the Jira token secret to Cloud Run

```bash
gcloud run services update jira-quality-report \
  --region us-central1 \
  --set-secrets=JIRA_API_TOKEN=jira-api-token:latest
```

### 4. Schedule with Cloud Scheduler (recommended)

```bash
# Create a service account for the scheduler
gcloud iam service-accounts create scheduler-invoker --display-name="Cloud Scheduler Invoker"

# Grant it permission to call Cloud Run
SERVICE_URL=$(gcloud run services describe jira-quality-report --region us-central1 --format 'value(status.url)')

gcloud run services add-iam-policy-binding jira-quality-report \
  --region us-central1 \
  --member="serviceAccount:scheduler-invoker@YOUR_PROJECT.iam.gserviceaccount.com" \
  --role="roles/run.invoker"

# Create the scheduled job (every Monday at 8 AM UTC)
gcloud scheduler jobs create http jira-quality-weekly \
  --schedule="0 8 * * 1" \
  --uri="${SERVICE_URL}/run" \
  --http-method=POST \
  --headers="X-API-Secret=YOUR_API_SECRET,Content-Type=application/json" \
  --body='{}' \
  --oidc-service-account-email="scheduler-invoker@YOUR_PROJECT.iam.gserviceaccount.com" \
  --oidc-token-audience="${SERVICE_URL}" \
  --attempt-deadline=3600s \
  --time-zone="America/Chicago"
```

## API Endpoints

| Endpoint     | Method | Description                                       |
|-------------|--------|---------------------------------------------------|
| `/`         | GET    | Health check                                      |
| `/run`      | POST   | Run report synchronously (waits for completion)   |
| `/run-async`| POST   | Start report in background, returns immediately   |
| `/status`   | GET    | Check if a job is running + last result            |

**POST `/run` body (optional):**
```json
{
  "epic_keys": ["CTLEP-1461", "CTLEP-1831"],
  "output_file": "custom_report.xlsx"
}
```

## Environment Variables

| Variable                       | Default              | Description                     |
|-------------------------------|----------------------|---------------------------------|
| `JIRA_BASE_URL`              | *(required)*         | Jira instance URL               |
| `JIRA_EMAIL`                 | *(required)*         | Jira account email              |
| `JIRA_API_TOKEN`             | *(secret)*           | Jira API token                  |
| `API_SECRET`                 | *(recommended)*      | Shared secret for HTTP auth     |
| `ENABLE_BIGQUERY_UPLOAD`     | `true`               | Upload results to BigQuery      |
| `ALL_EPICS_BIGQUERY_TABLE_ID`| *(required)*         | BigQuery target table           |
| `EPIC_PARALLEL_WORKERS`      | `4`                  | Parallel epic threads           |
| `LLM_PARALLEL_WORKERS`       | `8`                  | Parallel LLM scoring threads    |
| `LOG_LEVEL`                  | `INFO`               | Logging verbosity               |

## Logs

```bash
gcloud run services logs read jira-quality-report --region us-central1 --limit 200
```
