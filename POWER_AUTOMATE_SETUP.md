# Power Automate + Cloud Run Setup Guide

## Architecture

```
┌──────────────────────┐     HTTP POST      ┌──────────────────────┐
│   Power Automate     │ ─────────────────▶  │   Google Cloud Run   │
│   (Scheduled Flow)   │                     │   (Python Script)    │
│                      │  ◀───────────────── │                      │
│   - Runs on schedule │     JSON Response   │   - Jira API calls   │
│   - No laptop needed │                     │   - Vertex AI (LLM)  │
│   - Sends alerts     │                     │   - BigQuery upload  │
└──────────────────────┘                     └──────────────────────┘
```

Your laptop does NOT need to be on. Everything runs in the cloud.

---

## Step 1: Deploy to Google Cloud Run

### Prerequisites
- [Google Cloud SDK (gcloud CLI)](https://cloud.google.com/sdk/docs/install) installed
- Authenticated: `gcloud auth login`
- Project set: `gcloud config set project prj-mm-genai-qa-001`
- Artifact Registry API enabled: `gcloud services enable artifactregistry.googleapis.com run.googleapis.com cloudbuild.googleapis.com`

### Deploy

```powershell
cd cloud_run
.\deploy.ps1 -ApiSecret "YOUR_STRONG_SECRET_HERE"
```

### Store Jira API Token as a GCP Secret

Do NOT hardcode the Jira token. Use GCP Secret Manager:

```powershell
# Create the secret
echo "YOUR_JIRA_API_TOKEN" | gcloud secrets create jira-api-token --data-file=-

# Grant Cloud Run access
gcloud secrets add-iam-policy-binding jira-api-token `
    --member="serviceAccount:YOUR_SERVICE_ACCOUNT@prj-mm-genai-qa-001.iam.gserviceaccount.com" `
    --role="roles/secretmanager.secretAccessor"

# Bind secret to Cloud Run service
gcloud run services update jira-quality-report `
    --region us-central1 `
    --set-secrets=JIRA_API_TOKEN=jira-api-token:latest
```

### Get Your Service URL

```powershell
gcloud run services describe jira-quality-report --region us-central1 --format "value(status.url)"
```

This gives you something like: `https://jira-quality-report-xxxxx-uc.a.run.app`

---

## Step 2: Create a Service Account for Power Automate

Power Automate needs an identity token to call the Cloud Run service.

```powershell
# Create service account
gcloud iam service-accounts create power-automate-caller `
    --display-name="Power Automate Caller"

# Grant it permission to invoke the Cloud Run service
gcloud run services add-iam-policy-binding jira-quality-report `
    --region us-central1 `
    --member="serviceAccount:power-automate-caller@prj-mm-genai-qa-001.iam.gserviceaccount.com" `
    --role="roles/run.invoker"

# Create a key file (download this, you'll need it in Power Automate)
gcloud iam service-accounts keys create power-automate-key.json `
    --iam-account=power-automate-caller@prj-mm-genai-qa-001.iam.gserviceaccount.com
```

---

## Step 3: Set Up Power Automate Flow

### Option A: Simple Scheduled Flow (Recommended)

1. Go to [Power Automate](https://make.powerautomate.com/)
2. Click **Create** → **Scheduled cloud flow**
3. Set your schedule (e.g., every Monday at 8:00 AM, or daily at 6:00 AM)
4. Add an **HTTP** action (Premium connector):

   | Setting        | Value                                                      |
   |----------------|------------------------------------------------------------|
   | Method         | `POST`                                                     |
   | URI            | `https://jira-quality-report-xxxxx-uc.a.run.app/run`       |
   | Headers        | `X-API-Secret`: `YOUR_STRONG_SECRET_HERE`                  |
   |                | `Content-Type`: `application/json`                         |
   | Body           | `{}` (empty JSON for defaults)                             |
   | Timeout        | Set to `PT60M` (60 minutes) under Settings → Timeout       |

   **To override epic keys** (optional):
   ```json
   {
       "epic_keys": ["CTLEP-1461", "CTLEP-1831", "CTLEP-2021"]
   }
   ```

5. Add a **Condition** to check if the response was successful:
   - `Status code` is equal to `200`

6. **If yes** — Add **Send an email (V2)** or **Post message in a chat or channel (Teams)**:
   - Subject: `Jira Quality Report - Completed`
   - Body: `Report completed. Processed @{body('HTTP')['epics_processed']} epics in @{body('HTTP')['elapsed_minutes']} minutes.`

7. **If no** — Add notification for failure:
   - Subject: `Jira Quality Report - FAILED`
   - Body: `Error: @{body('HTTP')['error']}`

### Option B: Async Flow (For very long runs)

If the report takes longer than Power Automate's HTTP timeout:

1. Use the `/run-async` endpoint instead:

   | Setting | Value                                                          |
   |---------|----------------------------------------------------------------|
   | Method  | `POST`                                                         |
   | URI     | `https://jira-quality-report-xxxxx-uc.a.run.app/run-async`     |

2. Add a **Do Until** loop that polls `/status`:
   - **HTTP GET** `https://jira-quality-report-xxxxx-uc.a.run.app/status`
   - Condition: `body('HTTP_Status')['running']` is equal to `false`
   - Add a **Delay** of 5 minutes between each poll
   - Set loop timeout to 2 hours

3. After the loop, check `body('HTTP_Status')['last_result']['status']`

---

## Step 4: Test the Flow

### Test locally first:
```powershell
cd cloud_run
pip install flask
$env:API_SECRET = "test123"
python app.py
# In another terminal:
# curl -X POST http://localhost:8080/run -H "X-API-Secret: test123" -H "Content-Type: application/json" -d "{}"
```

### Test the deployed service:
```powershell
$URL = gcloud run services describe jira-quality-report --region us-central1 --format "value(status.url)"
$TOKEN = gcloud auth print-identity-token

Invoke-RestMethod -Method POST `
    -Uri "$URL/run" `
    -Headers @{
        "Authorization" = "Bearer $TOKEN"
        "X-API-Secret" = "YOUR_SECRET"
        "Content-Type" = "application/json"
    } `
    -Body "{}"
```

### Test from Power Automate:
- Use the **Test** button in your flow
- Check the run history for response details

---

## Environment Variables Reference

Set these on the Cloud Run service via `--set-env-vars` or the GCP Console:

| Variable                    | Default                                              | Description                        |
|-----------------------------|------------------------------------------------------|------------------------------------|
| `JIRA_BASE_URL`            | `https://lumen.atlassian.net`                        | Jira instance URL                  |
| `JIRA_EMAIL`               | (required)                                           | Jira service account email         |
| `JIRA_API_TOKEN`           | (use GCP Secret Manager)                             | Jira API token                     |
| `API_SECRET`               | (recommended)                                        | Shared secret for HTTP auth        |
| `ENABLE_BIGQUERY_UPLOAD`   | `true`                                               | Upload results to BigQuery         |
| `BQ_CLEAR_TABLE_BEFORE_RUN`| `true`                                               | Truncate table before each run     |
| `EPIC_PARALLEL_WORKERS`    | `4`                                                  | Parallel epic processing threads   |
| `LLM_PARALLEL_WORKERS`     | `8`                                                  | Parallel LLM scoring threads       |
| `LOG_LEVEL`                | `INFO`                                               | Logging verbosity                  |

---

## Troubleshooting

### View Cloud Run logs:
```powershell
gcloud run services logs read jira-quality-report --region us-central1 --limit 100
```

### Check if service is healthy:
```powershell
$URL = gcloud run services describe jira-quality-report --region us-central1 --format "value(status.url)"
Invoke-RestMethod "$URL/"
```

### Common issues:
- **401 Unauthorized**: Check `API_SECRET` matches between Cloud Run env and Power Automate header
- **Timeout**: Increase Cloud Run timeout (`--timeout 3600`) and Power Automate HTTP timeout (`PT60M`)
- **Memory errors**: Increase Cloud Run memory (`--memory 4Gi`)
- **LLM failures**: Check Vertex AI quota and service account permissions
