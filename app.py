"""
Cloud Run HTTP wrapper for Jira Quality Report automation.
Triggered on a schedule via HTTP POST (Power Automate, Cloud Scheduler, etc.).
"""

import logging
import os
import threading
import time
import traceback
from datetime import datetime, timezone

from flask import Flask, jsonify, request

app = Flask(__name__)
logger = logging.getLogger("cloud_run")

# Simple in-memory job tracking (Cloud Run instances are ephemeral)
_current_job = {"running": False, "last_result": None}
_job_lock = threading.Lock()

# Shared secret for request authentication (set via env var)
API_SECRET = os.getenv("API_SECRET", "")


def _authenticate(req):
    """Validate the request carries the correct API secret."""
    if not API_SECRET:
        # No secret configured — allow (dev mode only)
        return True
    auth_header = req.headers.get("X-API-Secret", "")
    if auth_header == API_SECRET:
        return True
    # Also check Authorization: Bearer <token>
    bearer = req.headers.get("Authorization", "")
    if bearer.startswith("Bearer ") and bearer[7:] == API_SECRET:
        return True
    return False


@app.route("/", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "service": "jira-quality-report"})


@app.route("/run", methods=["POST"])
def run_report():
    """
    Synchronous endpoint — runs the full report and returns when done.
    Power Automate HTTP action should set a long timeout (e.g. 3600s).

    Optional JSON body:
    {
        "epic_keys": ["CTLEP-1461", ...],   // override epic list
        "output_file": "custom_name.xlsx"     // override output file
    }
    """
    if not _authenticate(request):
        return jsonify({"error": "Unauthorized"}), 401

    with _job_lock:
        if _current_job["running"]:
            return jsonify({
                "error": "A job is already running. Please wait for it to finish.",
            }), 409

        _current_job["running"] = True

    start = time.time()
    started_at = datetime.now(timezone.utc).isoformat()

    try:
        # Parse optional overrides from request body
        body = request.get_json(silent=True) or {}
        epic_keys_override = body.get("epic_keys")
        output_file_override = body.get("output_file")

        # Import the extraction module (deferred so env vars are set first)
        import jira_data_extraction_enhanced as extractor

        # Apply overrides if provided
        if epic_keys_override and isinstance(epic_keys_override, list):
            extractor.EPIC_KEYS = epic_keys_override
        if output_file_override:
            extractor.OUTPUT_FILE_EXCEL = output_file_override

        logger.info(
            f"Starting report: {len(extractor.EPIC_KEYS)} epics, "
            f"BQ table={extractor.ALL_EPICS_BIGQUERY_TABLE_ID}"
        )

        # Run the main extraction + scoring pipeline
        extractor.main()

        elapsed = time.time() - start
        result = {
            "status": "success",
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_minutes": round(elapsed / 60, 1),
            "epics_processed": len(extractor.EPIC_KEYS),
            "bigquery_table": extractor.ALL_EPICS_BIGQUERY_TABLE_ID,
        }
        with _job_lock:
            _current_job["last_result"] = result
        return jsonify(result), 200

    except Exception as e:
        elapsed = time.time() - start
        error_result = {
            "status": "error",
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_minutes": round(elapsed / 60, 1),
            "error": str(e),
            "traceback": traceback.format_exc(),
        }
        logger.error(f"Report failed: {e}\n{traceback.format_exc()}")
        with _job_lock:
            _current_job["last_result"] = error_result
        return jsonify(error_result), 500

    finally:
        with _job_lock:
            _current_job["running"] = False


@app.route("/run-async", methods=["POST"])
def run_report_async():
    """
    Asynchronous endpoint — starts the job in a background thread and
    returns immediately. Use /status to check progress.
    Useful if Power Automate timeout is shorter than job duration.
    """
    if not _authenticate(request):
        return jsonify({"error": "Unauthorized"}), 401

    with _job_lock:
        if _current_job["running"]:
            return jsonify({
                "error": "A job is already running. Use /status to check progress.",
            }), 409

        _current_job["running"] = True
        _current_job["last_result"] = None

    body = request.get_json(silent=True) or {}

    def _background():
        start = time.time()
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            import jira_data_extraction_enhanced as extractor

            epic_keys_override = body.get("epic_keys")
            output_file_override = body.get("output_file")
            if epic_keys_override and isinstance(epic_keys_override, list):
                extractor.EPIC_KEYS = epic_keys_override
            if output_file_override:
                extractor.OUTPUT_FILE_EXCEL = output_file_override

            extractor.main()

            elapsed = time.time() - start
            result = {
                "status": "success",
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_minutes": round(elapsed / 60, 1),
                "epics_processed": len(extractor.EPIC_KEYS),
            }
        except Exception as e:
            elapsed = time.time() - start
            result = {
                "status": "error",
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_minutes": round(elapsed / 60, 1),
                "error": str(e),
            }
            logger.error(f"Background job failed: {e}")
        finally:
            with _job_lock:
                _current_job["running"] = False
                _current_job["last_result"] = result

    thread = threading.Thread(target=_background, daemon=True)
    thread.start()

    return jsonify({
        "status": "started",
        "message": "Job started. Poll /status for progress.",
    }), 202


@app.route("/status", methods=["GET"])
def job_status():
    """Check if a job is running and get last result."""
    if not _authenticate(request):
        return jsonify({"error": "Unauthorized"}), 401

    with _job_lock:
        return jsonify({
            "running": _current_job["running"],
            "last_result": _current_job["last_result"],
        })


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
