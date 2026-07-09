FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (leverages Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py ./

# Create output directory
RUN mkdir -p /tmp/output /tmp/logs

# Set default environment variables
ENV OUTPUT_FOLDER=/tmp/output
ENV LOG_FOLDER=/tmp/logs
ENV PYTHONUNBUFFERED=1

# Cloud Run Jobs execute the container and expect it to exit
CMD ["python", "main.py"]
