# Jira Epic Quality Report — Automated Cloud Run Job

An automated pipeline that extracts Jira epic hierarchies, scores feature quality using **Vertex AI (Gemini 2.5 Flash)**, and uploads structured results to **BigQuery**. Runs unattended on **GCP Cloud Run Jobs**, scheduled via **Cloud Scheduler**, and deployed through **Jenkins CI/CD**.

---

## Table of Contents

- [What This Project Does](#what-this-project-does)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [How It Works — Step by Step](#how-it-works--step-by-step)
- [Prerequisites](#prerequisites)
- [Local Development Setup](#local-development-setup)
- [Running Locally](#running-locally)
- [Cloud Deployment (Jenkins CI/CD)](#cloud-deployment-jenkins-cicd)
- [Cloud Scheduler (Automated Runs)](#cloud-scheduler-automated-runs)
- [Manual Execution](#manual-execution)
- [Environment Variables Reference](#environment-variables-reference)
- [Managing Epic Keys](#managing-epic-keys)
- [BigQuery Output Schema](#bigquery-output-schema)
- [Viewing Logs](#viewing-logs)
- [Troubleshooting](#troubleshooting)
- [Key GCP Resources](#key-gcp-resources)

---

## What This Project Does

For each Jira epic listed in `epic_keys.txt`, the script:

1. **Fetches the full hierarchy** from Jira: Epic → Capabilities → Features → Stories
2. **Scores each feature** using Vertex AI (Gemini 2.5 Flash) across 8 quality metrics:
   - Acceptance Criteria, Description, Story Points, Story Breakdown, Dependencies, Risks, NFRs, Overall Quality
3. **Uploads results** to a BigQuery table for reporting and dashboards
4. **Optionally exports** to Excel files (per-epic or combined)

This replaces manual quality reviews and runs automatically every week without anyone's laptop needing to be on.

---

## Architecture

```
┌─────────────────────┐
│   Cloud Scheduler   │  ← Triggers every Wednesday 9 PM IST
│   (cron job)        │
└────────┬────────────┘
         │ HTTP POST
         ▼
┌─────────────────────┐
│  Cloud Run Job      │  ← Containerized Python script
│  (epic-data-        │
│   automation)       │
└────────┬────────────┘
         │
    ┌────┼──────────────────┐
    │    │                  │
    ▼    ▼                  ▼
┌──────┐ ┌──────────┐ ┌──────────┐
│ Jira │ │ Vertex AI│ │ BigQuery │
│ REST │ │ Gemini   │ │          │
│ API  │ │ 2.5 Flash│ │          │
└──────┘ └──────────┘ └──────────┘
```

**CI/CD Flow:**
```
Git Push → Jenkins → Docker Build → Nexus Registry → GCP Artifact Registry → Cloud Run Job Update
```

---

## Project Structure

```
├── jira_data_extraction_enhanced.py    # Main script: Jira fetch + LLM scoring + BigQuery upload
├── epic_keys.txt                       # List of epic keys to process (one per line)
├── requirements.txt                    # Python dependencies
├── Dockerfile                          # Container image definition
├── Jenkinsfile                         # CI/CD pipeline definition
├── cicd/jenkins/jenkins_config/
│   ├── jenkins_config_dev.properties   # Dev environment config
│   ├── jenkins_config_qa.properties    # QA environment config
│   └── jenkins_config_prod.properties  # Prod environment config
├── .env.example                        # Template for local environment variables
├── .dockerignore                       # Files excluded from Docker build
├── .gitignore                          # Files excluded from Git
├── logs/                               # Local log output directory
│   └── .gitkeep
└── README.md                           # This file
```

---

## How It Works — Step by Step

### 1. Epic Key Resolution
The script reads epic keys from (in priority order):
1. `EPIC_KEYS_CSV` env var (comma-separated)
2. `EPIC_KEYS_FILE` env var (path to a file)
3. `epic_keys.txt` in the working directory
4. Hardcoded `DEFAULT_EPIC_KEYS` fallback

### 2. Jira Hierarchy Fetch (Parallel)
For each epic, the script fetches the full hierarchy using the Jira REST API:
```
Epic (e.g. CTLEP-1461)
 └── Capabilities (child issues of type "Capability")
      └── Features (child issues of type "Feature")
           └── Stories (child issues of type "Story")
```
- Uses `ThreadPoolExecutor` with configurable parallelism (`JIRA_FETCH_WORKERS=12`)
- Built-in retry logic (5 retries with exponential backoff)
- Rate limit handling (429 responses)

### 3. LLM Quality Scoring
Each feature is scored by Vertex AI Gemini 2.5 Flash:
- Sends feature description, acceptance criteria, story points, and a sample of child stories
- LLM returns a JSON object with 8 quality scores (1–10) plus improvement suggestions
- Runs in parallel (`LLM_PARALLEL_WORKERS=8`) with timeout and retry logic

### 4. BigQuery Upload
- Truncates the target table at the start of each run (`BQ_CLEAR_TABLE_BEFORE_RUN=true`)
- Appends rows as each epic completes (`BQ_APPEND_PER_EPIC=true`)
- Schema has 25 columns covering epic/capability/feature metadata, all 8 quality scores, and LLM suggestions

### 5. Optional Excel Export
- `SAVE_COMBINED_EXCEL=true` → one Excel file with all epics
- `SAVE_PER_EPIC_EXCEL=true` → separate Excel file per epic
- Color-coded cells: green (≥8), yellow (5–7), red (≤4)

---

## Prerequisites

### Tools
- **Python 3.11+** (for local development)
- **Docker** (for building container images)
- **gcloud CLI** (for GCP interactions)
- **Git** (for version control)

### GCP Access
- Access to GCP project `prj-mm-genai-qa-001`
- `gcloud auth login` and `gcloud config set project prj-mm-genai-qa-001`

### Jira Access
- A Jira account with read access to the relevant projects
- A [Jira API token](https://id.atlassian.com/manage-profile/security/api-tokens)

---

## Local Development Setup

### 1. Clone the repository
```bash
git clone https://github.com/AnmolTripathiLumen/EPIC_DATA_AUTOMATION.git
cd EPIC_DATA_AUTOMATION
```

### 2. Create a virtual environment
```bash
python -m venv .venv

# Windows
.venv\Scripts\Activate.ps1

# macOS/Linux
source .venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Set up environment variables
```bash
# Copy the template
cp .env.example .env

# Edit .env with your values:
# - JIRA_BASE_URL=https://lumen.atlassian.net
# - JIRA_EMAIL=your-email@lumen.com
# - JIRA_API_TOKEN=your-jira-api-token
# - GCP_PROJECT_ID=prj-mm-genai-qa-001
# - ALL_EPICS_BIGQUERY_TABLE_ID=prj-mm-genai-qa-001.All_Epic_Report.All_epics
```

### 5. Authenticate with GCP
```bash
gcloud auth application-default login
```

---

## Running Locally

```bash
# Run with all epics from epic_keys.txt
python jira_data_extraction_enhanced.py

# Run with specific epics (override via env var)
EPIC_KEYS_CSV="CTLEP-1461,CTLEP-1831" python jira_data_extraction_enhanced.py

# Run with Excel output enabled
SAVE_COMBINED_EXCEL=true python jira_data_extraction_enhanced.py
```

**Note:** The script uses `python-dotenv` to automatically load variables from a `.env` file if present.

---

## Cloud Deployment (Jenkins CI/CD)

The project uses Jenkins with the `jsl-jenkins-shared-library` for CI/CD. Pushing to `main` auto-triggers a build.

### Jenkins Pipeline URL
```
https://jenkinsprod.corp.intranet:8443/job/MMGENAI/job/EPIC_DATA_AUTOMATION/job/main/
```

### Pipeline Stages

| Stage | What It Does |
|---|---|
| **Init Parameters** | Resolves `DEPLOY_ENV` (dev/qa/prod) and generates `IMAGE_TAG` from branch + build number |
| **Load Properties** | Reads config from `cicd/jenkins/jenkins_config/jenkins_config_<env>.properties` |
| **Authorize** | *(Prod only)* Requires approval via `jslDeploymentControlKnob` |
| **Create Images** | Builds Docker image and pushes to Nexus (`nexusprod.corp.intranet:4567`) |
| **Copy to Artifact Registry** | Copies image from Nexus to GCP Artifact Registry |
| **Deploy** | Creates or updates the Cloud Run Job with env vars and secrets |

### How to Deploy

**Automatic:** Push to `main` → Jenkins auto-builds and deploys to dev.

**Manual:** Go to the Jenkins pipeline URL → click **"Build with Parameters"** → select `DEPLOY_ENV` (dev/qa/prod).

### Configuration Files

Each environment has its own properties file at `cicd/jenkins/jenkins_config/`:

| Property | Description |
|---|---|
| `GCP_PROJECT` | GCP project ID |
| `GCP_CICD_CREDENTIALS` | Jenkins credential ID for GCP service account |
| `AR_DOCKER_REPO` | Artifact Registry Docker repository path |
| `PROJECT_NAME` | Cloud Run Job name (`epic-data-automation`) |
| `VPC_CONNECTOR` | VPC connector for internal network access |
| `JIRA_BASE_URL` | Jira instance URL |
| `JIRA_EMAIL` | Jira service account email |
| `BQ_TABLE_ID` | BigQuery destination table |
| `JIRA_SECRET_NAME` | Secret Manager secret name for Jira API token |

### Secrets Management

The Jira API token is stored in **GCP Secret Manager** and mounted as an env var on the Cloud Run Job:

```bash
# Create the secret (one-time setup)
echo "YOUR_JIRA_API_TOKEN" | gcloud secrets create jira-api-token \
  --data-file=- \
  --replication-policy=user-managed \
  --locations=us-central1 \
  --project=prj-mm-genai-qa-001

# Grant the Cloud Run service account access
gcloud secrets add-iam-policy-binding jira-api-token \
  --member="serviceAccount:sa-aiops@prj-mm-genai-qa-001.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor" \
  --project=prj-mm-genai-qa-001
```

---

## Cloud Scheduler (Automated Runs)

The Cloud Run Job is triggered automatically by **Cloud Scheduler**:

| Setting | Value |
|---|---|
| **Scheduler Name** | `epic-data-automation-scheduler` |
| **Schedule** | Every Wednesday at 9:00 PM IST |
| **Cron Expression** | `0 21 * * 3` |
| **Timezone** | `Asia/Kolkata` |

### Check Scheduler Status

```bash
# CLI
gcloud scheduler jobs describe epic-data-automation-scheduler \
  --location=us-central1 --project=prj-mm-genai-qa-001

# Console
# https://console.cloud.google.com/cloudscheduler?project=prj-mm-genai-qa-001
```

### Modify the Schedule

```bash
gcloud scheduler jobs update http epic-data-automation-scheduler \
  --location=us-central1 \
  --schedule="0 9 * * 1" \
  --time-zone="America/Chicago" \
  --project=prj-mm-genai-qa-001
```

### Create a New Scheduler (if needed)

```bash
gcloud scheduler jobs create http epic-data-automation-scheduler \
  --location=us-central1 \
  --schedule="0 21 * * 3" \
  --time-zone="Asia/Kolkata" \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/prj-mm-genai-qa-001/jobs/epic-data-automation:run" \
  --http-method=POST \
  --oauth-service-account-email="sa-cicd@prj-mm-genai-qa-001.iam.gserviceaccount.com" \
  --project=prj-mm-genai-qa-001
```

---

## Manual Execution

You can trigger the job manually anytime without waiting for the schedule:

```bash
# Via gcloud CLI
gcloud run jobs execute epic-data-automation --region us-central1

# Via GCP Console
# https://console.cloud.google.com/run/jobs/us-central1/epic-data-automation?project=prj-mm-genai-qa-001
```

---

## Environment Variables Reference

### Required (set via Jenkinsfile on Cloud Run Job)

| Variable | Description |
|---|---|
| `JIRA_BASE_URL` | Jira instance URL (e.g., `https://lumen.atlassian.net`) |
| `JIRA_EMAIL` | Jira account email for API authentication |
| `JIRA_API_TOKEN` | Jira API token (injected from Secret Manager) |
| `GCP_PROJECT_ID` | GCP project ID for Vertex AI and BigQuery |
| `ALL_EPICS_BIGQUERY_TABLE_ID` | BigQuery table in `project.dataset.table` format |

### Optional (with defaults)

| Variable | Default | Description |
|---|---|---|
| `GCP_LOCATION` | `us-central1` | GCP region for Vertex AI |
| `ENABLE_BIGQUERY_UPLOAD` | `true` | Upload results to BigQuery |
| `BQ_APPEND_PER_EPIC` | `true` | Append rows after each epic (vs bulk at end) |
| `BQ_CLEAR_TABLE_BEFORE_RUN` | `true` | Truncate BigQuery table before starting |
| `SAVE_COMBINED_EXCEL` | `false` | Generate a combined Excel report |
| `SAVE_PER_EPIC_EXCEL` | `false` | Generate per-epic Excel reports |
| `EPIC_KEYS_CSV` | *(empty)* | Override epic keys as comma-separated values |
| `EPIC_KEYS_FILE` | *(empty)* | Override path to epic keys file |
| `EPIC_PARALLEL_WORKERS` | `4` | Number of epics processed in parallel |
| `LLM_PARALLEL_WORKERS` | `8` | Number of concurrent LLM scoring calls |
| `JIRA_FETCH_WORKERS` | `12` | Number of concurrent Jira API calls |
| `LLM_TIMEOUT_SECONDS` | `75` | Timeout per LLM call (seconds) |
| `LLM_MAX_ATTEMPTS` | `3` | Max retry attempts for failed LLM calls |
| `LLM_RECOVERY_TIMEOUT_SECONDS` | `120` | Extended timeout on retries |
| `LLM_STORY_SAMPLE_SIZE` | `6` | Number of stories sent to LLM per feature |
| `LLM_DESC_MAX_CHARS` | `450` | Max description chars sent to LLM |
| `LLM_MAX_OUTPUT_TOKENS` | `8192` | Max tokens in LLM response |
| `JIRA_API_DELAY` | `0.05` | Delay between Jira API calls (seconds) |
| `JIRA_REQUEST_TIMEOUT` | `30` | Jira HTTP request timeout (seconds) |
| `CAPABILITY_LIMIT` | `0` | Max capabilities per epic (0 = unlimited) |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `RUN_LOG_FILE` | `run.log` | Log file path |

---

## Managing Epic Keys

### Adding Epics
1. Open `epic_keys.txt`
2. Add the new epic key on a new line (e.g., `CTLEP-9999`)
3. Commit, push, and let Jenkins redeploy:
   ```bash
   git add epic_keys.txt
   git commit -m "Add epic CTLEP-9999"
   git push origin main
   ```

### Removing Epics
1. Delete the line from `epic_keys.txt`
2. Commit and push — Jenkins will rebuild the container with the updated file

### Supported Formats
- One key per line
- No comments or headers
- Example keys: `CTLEP-1461`, `CLT-24`, `STARBP-41`

---

## BigQuery Output Schema

The results are written to `prj-mm-genai-qa-001.All_Epic_Report.All_epics` with 25 columns:

| Column | Type | Description |
|---|---|---|
| `epic_key` | STRING | Jira epic key |
| `epic_summary` | STRING | Epic title |
| `capability_key` | STRING | Capability issue key |
| `capability_summary` | STRING | Capability title |
| `feature_key` | STRING | Feature issue key |
| `feature_summary` | STRING | Feature title |
| `feature_status` | STRING | Feature status (e.g., In Progress) |
| `fix_versions` | STRING | Fix versions assigned to the feature |
| `story_count` | INTEGER | Number of child stories |
| `total_story_points` | FLOAT | Sum of story points across stories |
| `avg_story_points` | FLOAT | Average story points per story |
| `stories_without_points` | INTEGER | Stories missing story point estimates |
| `acceptance_criteria_score` | INTEGER | LLM score (1–10) |
| `description_score` | INTEGER | LLM score (1–10) |
| `story_points_score` | INTEGER | LLM score (1–10) |
| `story_breakdown_score` | INTEGER | LLM score (1–10) |
| `dependency_score` | INTEGER | LLM score (1–10) |
| `risk_score` | INTEGER | LLM score (1–10) |
| `nfr_score` | INTEGER | LLM score (1–10) |
| `overall_quality_score` | INTEGER | LLM overall score (1–10) |
| `improvement_suggestions` | STRING | LLM recommendations |
| `is_planning_set` | BOOLEAN | Whether epic is in the planning set |
| `is_q3_scoped` | BOOLEAN | Whether feature has Q3 2026 fix version |
| `team_avg_features_per_fv` | FLOAT | Team historical avg features per fix version |
| `team_avg_sp_per_fv` | FLOAT | Team historical avg story points per fix version |

---

## Viewing Logs

### Cloud Run Job Logs (production)
```bash
# Recent logs
gcloud logging read \
  "resource.type=cloud_run_job AND resource.labels.job_name=epic-data-automation" \
  --project=prj-mm-genai-qa-001 \
  --limit=200 \
  --format="table(timestamp,textPayload)"

# Filter for errors only
gcloud logging read \
  "resource.type=cloud_run_job AND resource.labels.job_name=epic-data-automation AND severity>=ERROR" \
  --project=prj-mm-genai-qa-001 \
  --limit=50

# View in GCP Console
# https://console.cloud.google.com/logs/query?project=prj-mm-genai-qa-001
# Filter: resource.type="cloud_run_job" resource.labels.job_name="epic-data-automation"
```

### Cloud Scheduler Logs
```bash
gcloud scheduler jobs describe epic-data-automation-scheduler \
  --location=us-central1 --project=prj-mm-genai-qa-001
```

### Local Logs
When running locally, logs are written to both the console and `logs/run.log`.

---

## Troubleshooting

### Common Issues

| Issue | Cause | Fix |
|---|---|---|
| `BigQuery.Client(project="")` error | `GCP_PROJECT_ID` env var not set on Cloud Run Job | Ensure Jenkinsfile includes `GCP_PROJECT_ID` in `jobEnvVars`, redeploy |
| `PermissionDenied` on Secret Manager | Service account missing `secretmanager.secretAccessor` role | Grant role to `sa-aiops@...` on the secret |
| `constraints/gcp.resourceLocations` error | Org policy blocks global secrets | Use `--replication-policy=user-managed --locations=us-central1` |
| `429 Too Many Requests` from Jira | Rate limited | Script handles this automatically with exponential backoff |
| LLM scoring returns empty/malformed JSON | Gemini timeout or quota | Script retries up to `LLM_MAX_ATTEMPTS` times with extended timeout |
| Jenkins build fails at Wiz scan | Security vulnerabilities in Docker image | Review Wiz scan results and fix vulnerable dependencies |
| `NullPointerException` in Jenkins post-build | Missing `deploy_auth_token` credential | Non-critical — deployment still succeeds, notification step fails |

### Checking Job Execution Status
```bash
# List recent executions
gcloud run jobs executions list --job=epic-data-automation \
  --region=us-central1 --project=prj-mm-genai-qa-001

# Describe a specific execution
gcloud run jobs executions describe <EXECUTION_NAME> \
  --region=us-central1 --project=prj-mm-genai-qa-001
```

---

## Key GCP Resources

| Resource | Value |
|---|---|
| **GCP Project** | `prj-mm-genai-qa-001` |
| **Region** | `us-central1` |
| **Cloud Run Job** | `epic-data-automation` |
| **Cloud Scheduler** | `epic-data-automation-scheduler` |
| **Secret** | `jira-api-token` (Secret Manager) |
| **BigQuery Table** | `prj-mm-genai-qa-001.All_Epic_Report.All_epics` |
| **Artifact Registry** | `us-central1-docker.pkg.dev/prj-mm-genai-qa-001/cloud-run-source-deploy` |
| **Service Account (runtime)** | `sa-aiops@prj-mm-genai-qa-001.iam.gserviceaccount.com` |
| **Service Account (CI/CD)** | `sa-cicd@prj-mm-genai-qa-001.iam.gserviceaccount.com` |
| **VPC Connector** | `projects/prj-mm-genai-qa-001/locations/us-central1/connectors/slc-genai-qa-uscentral-1` |
| **Jenkins Pipeline** | `https://jenkinsprod.corp.intranet:8443/job/MMGENAI/job/EPIC_DATA_AUTOMATION/` |
| **GitHub Repo** | `https://github.com/AnmolTripathiLumen/EPIC_DATA_AUTOMATION` |
