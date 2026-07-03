FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY jira_data_extraction_enhanced.py .
COPY epic_keys.txt .

ENV PYTHONUNBUFFERED=1

CMD ["python", "jira_data_extraction_enhanced.py"]
