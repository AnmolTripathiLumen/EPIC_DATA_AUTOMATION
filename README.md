# Jira Epic Quality Report — Cloud Run Job

Automated Jira epic quality scoring. Fetches epic hierarchy from Jira, scores features with Vertex AI (Gemini), and uploads results to BigQuery. Deployed as a **Cloud Run Job** via Jenkins CI/CD.

## Architecture

```
Cloud Scheduler (cron)
  → gcloud run jobs execute
    → Cloud Run Job (this repo)
        ├── Jira REST API  (fetch epics/features/stories)
        ├── Vertex AI      (LLM quality scoring)
        └── BigQuery       (upload results)
```

## Project Structure

```
├── jira_data_extraction_enhanced.py   # Core: Jira fetch + LLM scoring + BigQuery upload
├── epic_keys.txt                      # Epic keys to process (one per line)
├── requirements.txt                   # Python dependencies
├── Dockerfile                         # Container image (runs script directly)
├── Jenkinsfile                        # CI/CD pipeline (build → push → deploy)
├── cicd/jenkins/jenkins_config/
│   ├── jenkins_config_dev.properties  # Dev environment config
│   ├── jenkins_config_qa.properties   # QA environment config
│   └── jenkins_config_prod.properties # Prod environment config
├── .env.example                       # Environment variable template
├── .dockerignore
└── .gitignore
```

## Deployment via Jenkins

### Prerequisites

- Jira API token stored in GCP Secret Manager:
  ```bash
  "YOUR_TOKEN" | gcloud secrets create jira-api-token \
    --replication-policy=user-managed --locations=us-central1 --data-file=-
  ```
- Jenkins properties files updated with correct values for your environment

### Pipeline Stages

1. **Init Parameters** — resolve deploy env and image tag
2. **Load Properties** — read env-specific config from `cicd/jenkins/jenkins_config/`
3. **Create Images** — build Docker image, push to Nexus
4. **Copy to Artifact Registry** — `jslNexusToGcpCopy`
5. **Deploy** — create or update Cloud Run Job with env vars and secrets

### Trigger a Build

- Push to branch or manually trigger in Jenkins with `DEPLOY_ENV` parameter (`dev`/`qa`/`prod`)

## Schedule with Cloud Scheduler

After the Cloud Run Job is deployed, set up a recurring schedule:

```bash
# Create a service account for the scheduler
gcloud iam service-accounts create scheduler-invoker \
  --display-name="Cloud Scheduler Invoker"

# Grant permission to execute the Cloud Run Job
gcloud run jobs add-iam-policy-binding epic-data-automation \
  --region us-central1 \
  --member="serviceAccount:scheduler-invoker@prj-mm-genai-qa-001.iam.gserviceaccount.com" \
  --role="roles/run.invoker"

# Create the cron job (every Monday at 8 AM CT)
gcloud scheduler jobs create http jira-quality-weekly \
  --schedule="0 8 * * 1" \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/prj-mm-genai-qa-001/jobs/epic-data-automation:run" \
  --http-method=POST \
  --oauth-service-account-email="scheduler-invoker@prj-mm-genai-qa-001.iam.gserviceaccount.com" \
  --time-zone="America/Chicago"
```

Or trigger manually anytime:
```bash
gcloud run jobs execute epic-data-automation --region us-central1
```

## Environment Variables

Set on the Cloud Run Job via Jenkinsfile `--set-env-vars`:

| Variable                       | Default              | Description                     |
|-------------------------------|----------------------|---------------------------------|
| `JIRA_BASE_URL`              | *(required)*         | Jira instance URL               |
| `JIRA_EMAIL`                 | *(required)*         | Jira account email              |
| `JIRA_API_TOKEN`             | *(secret)*           | Jira API token (Secret Manager) |
| `ENABLE_BIGQUERY_UPLOAD`     | `true`               | Upload results to BigQuery      |
| `ALL_EPICS_BIGQUERY_TABLE_ID`| *(required)*         | BigQuery target table           |
| `EPIC_PARALLEL_WORKERS`      | `4`                  | Parallel epic threads           |
| `LLM_PARALLEL_WORKERS`       | `8`                  | Parallel LLM scoring threads    |
| `LOG_LEVEL`                  | `INFO`               | Logging verbosity               |

## Adding / Removing Epics

Edit `epic_keys.txt` — one key per line. Rebuild and redeploy via Jenkins.

## Logs

```bash
# View Cloud Run Job execution logs
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=epic-data-automation" \
  --project=prj-mm-genai-qa-001 --limit=200 --format="table(timestamp,textPayload)"
```
