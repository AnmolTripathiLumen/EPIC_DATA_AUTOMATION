# Jira Epic Quality Report Automation

Automatically pulls Jira epic data, scores quality using Vertex AI (Gemini), and uploads results to BigQuery. Can run locally on a schedule or in the cloud via **Google Cloud Run + Power Automate** (no laptop required).

---

## Features

- Fetches full epic → capability → feature → story hierarchy from Jira
- AI-powered quality scoring (acceptance criteria, granularity, sizing, test coverage)
- Parallel processing for 80+ epics
- BigQuery upload for dashboards and reporting
- Optional Excel report export
- Cloud Run deployment for scheduled, unattended execution via Power Automate

---

## Project Structure

```
├── jira_data_extraction_enhanced.py   # Main script: Jira fetch + LLM scoring + BigQuery upload
├── run_all_epics.py                   # Python runner for processing all epics
├── run_all_epics.ps1                  # PowerShell wrapper for local/scheduled runs
├── epic_keys.txt                      # List of epic keys to process (one per line)
├── requirements.txt                   # Python dependencies
├── .env.example                       # Environment variable template
├── Dockerfile                         # Container image for Cloud Run
├── cloud_run/
│   ├── app.py                         # Flask HTTP wrapper for Cloud Run
│   ├── deploy.ps1                     # One-command Cloud Run deployment
│   └── requirements_cloud.txt         # Cloud Run extra dependencies (Flask, gunicorn)
├── POWER_AUTOMATE_SETUP.md            # Step-by-step Power Automate + Cloud Run guide
└── logs/                              # Runtime logs (git-ignored)
```

---

## Quick Start (Local)

### 1. Clone and install

```bash
git clone https://github.com/YOUR_ORG/jira-quality-report.git
cd jira-quality-report
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
# Edit .env with your Jira email, API token, etc.
```

Authenticate with Google Cloud (for Vertex AI + BigQuery):
```bash
gcloud auth application-default login
```

### 3. Run

```powershell
# Process all epics from epic_keys.txt
python run_all_epics.py --epics-file epic_keys.txt

# Or use the PowerShell wrapper
powershell -ExecutionPolicy Bypass -File .\run_all_epics.ps1
```

---

## Cloud Deployment (No Laptop Required)

Deploy to Google Cloud Run and trigger from Power Automate on a schedule.

See **[POWER_AUTOMATE_SETUP.md](POWER_AUTOMATE_SETUP.md)** for the full guide.

**TL;DR:**
1. `.\cloud_run\deploy.ps1 -ApiSecret "YOUR_SECRET"` — deploys to Cloud Run
2. Create a Scheduled Flow in Power Automate → HTTP POST to your Cloud Run URL
3. Results go to BigQuery automatically. No laptop needed.

---

## Configuration

All settings are controlled via environment variables (set in `.env` locally or Cloud Run env vars):

| Variable                    | Default                              | Description                          |
|-----------------------------|--------------------------------------|--------------------------------------|
| `JIRA_BASE_URL`            | *(required)*                         | Jira instance URL                    |
| `JIRA_EMAIL`               | *(required)*                         | Jira account email                   |
| `JIRA_API_TOKEN`           | *(required)*                         | Jira API token                       |
| `GCP_PROJECT_ID`           | *(required)*                         | Google Cloud project ID              |
| `GCP_LOCATION`             | `us-central1`                        | Vertex AI region                     |
| `ALL_EPICS_BIGQUERY_TABLE_ID` | *(required)*                      | BigQuery target table                |
| `ENABLE_BIGQUERY_UPLOAD`   | `true`                               | Upload results to BigQuery           |
| `SAVE_COMBINED_EXCEL`      | `false`                              | Save combined Excel report           |
| `EPIC_PARALLEL_WORKERS`    | `4`                                  | Parallel epic processing threads     |
| `LLM_PARALLEL_WORKERS`     | `8`                                  | Parallel LLM scoring threads         |
| `LOG_LEVEL`                | `INFO`                               | Logging verbosity                    |

---

## Adding / Removing Epics

Edit `epic_keys.txt` — one epic key per line. The next run will use the updated list.

---

## Requirements

- Python 3.10+
- Google Cloud SDK (`gcloud`) for Vertex AI and BigQuery authentication
- Jira Cloud account with API token
- *(For cloud deployment)* Docker, GCP project with Cloud Run enabled