<#
.SYNOPSIS
    Deploy the Jira Quality Report service to Google Cloud Run.
.DESCRIPTION
    Builds the Docker image via Cloud Build and deploys to Cloud Run.
    Requires: gcloud CLI authenticated with your GCP project.
.PARAMETER ProjectId
    GCP project ID (default: prj-mm-genai-qa-001)
.PARAMETER Region
    Cloud Run region (default: us-central1)
.PARAMETER ServiceName
    Cloud Run service name (default: jira-quality-report)
#>
param(
    [string]$ProjectId = "prj-mm-genai-qa-001",
    [string]$Region = "us-central1",
    [string]$ServiceName = "jira-quality-report",
    [string]$ApiSecret = ""
)

$ErrorActionPreference = "Stop"

# Validate gcloud is available
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    throw "gcloud CLI not found. Install from https://cloud.google.com/sdk/docs/install"
}

Write-Host "=== Deploying $ServiceName to Cloud Run ===" -ForegroundColor Cyan
Write-Host "Project:  $ProjectId"
Write-Host "Region:   $Region"
Write-Host "Service:  $ServiceName"

# Set project
gcloud config set project $ProjectId

# Build container image using Cloud Build
$imageName = "$Region-docker.pkg.dev/$ProjectId/cloud-run-source-deploy/$ServiceName"
Write-Host "`nBuilding container image..." -ForegroundColor Yellow

gcloud builds submit --tag $imageName --timeout=1200

# Deploy to Cloud Run
Write-Host "`nDeploying to Cloud Run..." -ForegroundColor Yellow

# Read env vars from .env.example defaults; override via GCP Console or --set-env-vars
$envVars = @(
    "ENABLE_BIGQUERY_UPLOAD=true",
    "BQ_APPEND_PER_EPIC=true",
    "BQ_CLEAR_TABLE_BEFORE_RUN=true",
    "SAVE_COMBINED_EXCEL=false",
    "SAVE_PER_EPIC_EXCEL=false",
    "LOG_LEVEL=INFO"
)

# Require JIRA_BASE_URL and JIRA_EMAIL as parameters or env vars
if ($env:JIRA_BASE_URL) { $envVars += "JIRA_BASE_URL=$($env:JIRA_BASE_URL)" }
if ($env:JIRA_EMAIL) { $envVars += "JIRA_EMAIL=$($env:JIRA_EMAIL)" }
if ($env:GCP_PROJECT_ID) { $envVars += "GCP_PROJECT_ID=$($env:GCP_PROJECT_ID)" }
if ($env:ALL_EPICS_BIGQUERY_TABLE_ID) { $envVars += "ALL_EPICS_BIGQUERY_TABLE_ID=$($env:ALL_EPICS_BIGQUERY_TABLE_ID)" }

if ($ApiSecret) {
    $envVars += "API_SECRET=$ApiSecret"
}

$envVarString = $envVars -join ","

gcloud run deploy $ServiceName `
    --image $imageName `
    --region $Region `
    --platform managed `
    --memory 2Gi `
    --cpu 2 `
    --timeout 3600 `
    --concurrency 1 `
    --max-instances 1 `
    --set-env-vars $envVarString `
    --no-allow-unauthenticated

Write-Host "`n=== Deployment Complete ===" -ForegroundColor Green
Write-Host "Get the service URL with:"
Write-Host "  gcloud run services describe $ServiceName --region $Region --format 'value(status.url)'"
Write-Host ""
Write-Host "IMPORTANT: Set JIRA_API_TOKEN as a secret:"
Write-Host "  1. Create secret: gcloud secrets create jira-api-token --data-file=-"
Write-Host "  2. Bind to service: gcloud run services update $ServiceName --region $Region --set-secrets=JIRA_API_TOKEN=jira-api-token:latest"
