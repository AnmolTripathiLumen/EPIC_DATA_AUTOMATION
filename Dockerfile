FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
COPY cloud_run/requirements_cloud.txt .
RUN pip install --no-cache-dir -r requirements.txt -r requirements_cloud.txt

# Copy application code
COPY jira_data_extraction_enhanced.py .
COPY run_all_epics.py .
COPY epic_keys.txt .
COPY cloud_run/app.py .

# Cloud Run uses PORT env var (default 8080)
ENV PORT=8080
ENV PYTHONUNBUFFERED=1

# Run with gunicorn for production reliability
# Timeout set to 3600s (1 hour) for long-running report jobs
CMD exec gunicorn --bind :$PORT --workers 1 --threads 2 --timeout 3600 app:app
