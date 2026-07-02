#!/usr/bin/env bash
# Deploy Jira Quality Report service to Google Cloud Run.
# Usage: bash deploy.sh [--project PROJECT_ID] [--region REGION] [--secret API_SECRET]
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-prj-mm-genai-qa-001}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-jira-quality-report}"
API_SECRET=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --project)  PROJECT_ID="$2";  shift 2 ;;
    --region)   REGION="$2";      shift 2 ;;
    --service)  SERVICE_NAME="$2"; shift 2 ;;
    --secret)   API_SECRET="$2";  shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

echo "=== Deploying ${SERVICE_NAME} to Cloud Run ==="
echo "Project:  ${PROJECT_ID}"
echo "Region:   ${REGION}"
echo "Service:  ${SERVICE_NAME}"

gcloud config set project "${PROJECT_ID}"

# ── Build container image ──────────────────────────────────────────
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE_NAME}"
echo ""
echo "Building container image..."
gcloud builds submit --tag "${IMAGE}" --timeout=1200

# ── Deploy to Cloud Run ───────────────────────────────────────────
echo ""
echo "Deploying to Cloud Run..."

ENV_VARS="JIRA_BASE_URL=https://lumen.atlassian.net"
ENV_VARS+=",JIRA_EMAIL=Anmol.manitripathi@lumen.com"
ENV_VARS+=",ENABLE_BIGQUERY_UPLOAD=true"
ENV_VARS+=",BQ_APPEND_PER_EPIC=true"
ENV_VARS+=",BQ_CLEAR_TABLE_BEFORE_RUN=true"
ENV_VARS+=",SAVE_COMBINED_EXCEL=false"
ENV_VARS+=",SAVE_PER_EPIC_EXCEL=false"
ENV_VARS+=",LOG_LEVEL=INFO"

if [[ -n "${API_SECRET}" ]]; then
  ENV_VARS+=",API_SECRET=${API_SECRET}"
fi

gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --platform managed \
  --memory 2Gi \
  --cpu 2 \
  --timeout 3600 \
  --concurrency 1 \
  --max-instances 1 \
  --set-env-vars "${ENV_VARS}" \
  --no-allow-unauthenticated

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Service URL:"
gcloud run services describe "${SERVICE_NAME}" --region "${REGION}" --format 'value(status.url)'
echo ""
echo "IMPORTANT: Store JIRA_API_TOKEN in Secret Manager:"
echo "  echo 'YOUR_TOKEN' | gcloud secrets create jira-api-token --data-file=-"
echo "  gcloud run services update ${SERVICE_NAME} --region ${REGION} --set-secrets=JIRA_API_TOKEN=jira-api-token:latest"
