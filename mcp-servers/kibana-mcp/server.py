"""
kibana-mcp: MCP server that provides log search capability to Agent 1.

In production: queries Elasticsearch/Kibana REST API.
In free-tier dev: serves from synthetic log store + real Docker container logs.

Tools:
  GET  /tools/search_logs          → logs for a service in ±N min window
  GET  /tools/get_error_summary    → top errors for a service
  GET  /tools/extract_root_cause   → parse the most likely root-cause error
  GET  /tools/list_tools           → tool manifest
  GET  /health                     → server health
"""

import json
import os
import re
import subprocess
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

from log_store import get_synthetic_logs
import urllib.request
import urllib.parse

app = FastAPI(title="kibana-mcp", version="1.0.0")

# ── Config ────────────────────────────────────────────────────────────────────
MCP_PORT          = int(os.getenv("MCP_PORT", "9002"))
ELASTICSEARCH_URL = os.getenv("ELASTICSEARCH_URL", "")   # empty = use synthetic store
LOG_MODE          = os.getenv("LOG_MODE", "synthetic")    # synthetic | docker | elasticsearch | loki
LOKI_URL          = os.getenv("LOKI_URL", "http://loki:3100")

# Map service names to Docker container names
SERVICE_TO_CONTAINER = {
    "payment-svc":       "payment-svc",
    "auth-svc":          "auth-svc",
    "loan-svc":          "loan-svc",
    "notification-svc":  "notification-svc",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_timestamp(ts_str: str) -> Optional[datetime]:
    """Parse ISO timestamp string to datetime."""
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return None


def within_window(log_ts: str, center_ts: datetime, window_minutes: int) -> bool:
    """Return True if log_ts falls within center_ts ± window_minutes."""
    dt = parse_timestamp(log_ts)
    if not dt:
        return False
    delta = timedelta(minutes=window_minutes)
    return (center_ts - delta) <= dt <= (center_ts + delta)


def fetch_docker_logs(service: str, since_minutes: int = 10) -> list:
    """
    Pull real logs from a running Docker container.
    Falls back to empty list if Docker is not accessible.
    """
    container = SERVICE_TO_CONTAINER.get(service)
    if not container:
        return []
    try:
        result = subprocess.run(
            ["docker", "logs", "--since", f"{since_minutes}m", "--timestamps", container],
            capture_output=True, text=True, timeout=5
        )
        raw_lines = (result.stdout + result.stderr).strip().split("\n")
        logs = []
        for line in raw_lines:
            if not line.strip():
                continue
            # Docker prepends a timestamp: "2026-04-07T05:12:33.123456789Z {json}"
            parts = line.split(" ", 1)
            docker_ts = parts[0] if len(parts) == 2 else ""
            content   = parts[1] if len(parts) == 2 else line

            # Try to parse as JSON (our services emit JSON logs)
            try:
                entry = json.loads(content)
                if "@timestamp" not in entry:
                    entry["@timestamp"] = docker_ts
            except json.JSONDecodeError:
                entry = {
                    "@timestamp": docker_ts,
                    "level": "INFO",
                    "service": service,
                    "message": content,
                }
            logs.append(entry)
        return logs
    except Exception:
        return []


def search_synthetic(
    service: str,
    center_ts: datetime,
    window_minutes: int,
    level_filter: Optional[str]
) -> list:
    """Search the synthetic log store."""
    all_logs = get_synthetic_logs()
    results = []
    for log in all_logs:
        if log.get("service", "").lower() != service.lower():
            continue
        if not within_window(log.get("@timestamp", ""), center_ts, window_minutes):
            continue
        if level_filter and log.get("level", "").upper() != level_filter.upper():
            continue
        results.append(log)
    return results




def _extract_exception_type(message: str) -> str:
    """Extract exception type from message like 'PaymentGatewayException: ...'"""
    import re
    if not message:
        return ""
    match = re.match(r'^([A-Z][a-zA-Z]+Exception|[A-Z][a-zA-Z]+Error)', message)
    if match:
        return match.group(1)
    return ""

def search_loki(service: str, center_ts: datetime, window_minutes: int,
                level_filter: str = None, alert_name: str = None) -> list:
    """
    Query Loki HTTP API for logs matching service within time window.
    Returns list of log dicts compatible with the synthetic store format.
    """
    start_ts = center_ts - timedelta(minutes=window_minutes)
    end_ts   = center_ts + timedelta(minutes=window_minutes)

    # Build LogQL query
    label_selector = f'{{service="{service}"}}'
    if level_filter:
        label_selector += f' |= "{level_filter}"'

    # Map alert_name to exception type for filtering
    if alert_name and alert_name in ALERT_EXCEPTION_MAP:
        target = ALERT_EXCEPTION_MAP[alert_name]
        targets = [target] if isinstance(target, str) else target
        label_selector += f' |= "{targets[0]}"'

    params = urllib.parse.urlencode({
        "query": label_selector,
        "start": str(int(start_ts.timestamp() * 1e9)),
        "end":   str(int(end_ts.timestamp() * 1e9)),
        "limit": "50",
        "direction": "backward",
    })

    url = f"{LOKI_URL}/loki/api/v1/query_range?{params}"

    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        logger.warning(f"Loki query failed: {e}")
        return []

    logs = []
    for stream in data.get("data", {}).get("result", []):
        labels = stream.get("stream", {})
        for ts_ns, line in stream.get("values", []):
            try:
                entry = json.loads(line)
            except Exception:
                entry = {"message": line}

            # Normalise to synthetic store format
            logs.append({
                "timestamp":      datetime.fromtimestamp(int(ts_ns) / 1e9,
                                  tz=timezone.utc).isoformat(),
                "level":          entry.get("level", labels.get("level", "INFO")),
                "service":        service,
                "exception_type": (
                                  entry.get("exception_type")
                                  or labels.get("exception_type")
                                  or _extract_exception_type(entry.get("message", line))
                                  or ""
                                  ),
                "message":        entry.get("message", line[:200]),
                "full_message":   line[:500],
                "source":         "loki",
            })

    return logs

def extract_root_cause(logs: list) -> dict:
    """
    Identify the most likely root-cause error from a list of log entries.
    Prioritises: ERROR level > exception_type present > earliest occurrence.
    """
    error_logs = [l for l in logs if l.get("level") == "ERROR"]
    if not error_logs:
        return {"found": False, "message": "No ERROR level logs found in window"}

    # Sort by timestamp ascending to get first occurrence
    error_logs.sort(key=lambda x: x.get("@timestamp", ""))
    first_error = error_logs[0]

    # Extract exception type if present
    exception_type = first_error.get("exception_type", "")
    message        = first_error.get("message", "")

    # Extract a clean short error description
    short_error = message.split(".")[0] if "." in message else message
    short_error = short_error[:200]  # cap length

    return {
        "found": True,
        "service": first_error.get("service"),
        "timestamp": first_error.get("@timestamp"),
        "level": first_error.get("level"),
        "exception_type": exception_type,
        "full_message": message,
        "short_error": short_error,
        "total_errors_in_window": len(error_logs),
        "recurrence_count": sum(
            1 for l in error_logs
            if exception_type and l.get("exception_type") == exception_type
        ),
        "raw_log": first_error,
    }


# ── Tool endpoints ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "up",
        "service": "kibana-mcp",
        "log_mode": LOG_MODE,
        "loki_url": LOKI_URL if LOG_MODE == "loki" else None,
        "synthetic_log_count": len(get_synthetic_logs()) if LOG_MODE == "synthetic" else 0,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/tools/search_logs")
async def search_logs(
    service:        str,
    alert_timestamp: Optional[str] = None,
    window_minutes: int = 5,
    level:          Optional[str] = None,
    limit:          int = 50,
):
    """
    Tool: search_logs
    Returns logs for a given service within ±window_minutes of the alert timestamp.
    Agent 1 calls this after get_alert_detail to find what caused the alert.

    Query params:
      service          required — e.g. 'payment-svc'
      alert_timestamp  ISO timestamp from grafana-mcp alert detail
                       (defaults to now if omitted)
      window_minutes   search window either side of timestamp (default 5)
      level            filter by log level: ERROR | WARN | INFO (optional)
      limit            max log lines to return (default 50)
    """
    # Parse or default center timestamp
    if alert_timestamp:
        center_ts = parse_timestamp(alert_timestamp)
        if not center_ts:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot parse alert_timestamp: '{alert_timestamp}'. "
                       "Use ISO format e.g. '2026-04-07T05:12:00+00:00'"
            )
    else:
        center_ts = datetime.now(timezone.utc)

    window_start = (center_ts - timedelta(minutes=window_minutes)).isoformat()
    window_end   = (center_ts + timedelta(minutes=window_minutes)).isoformat()

    # Choose log source
    if LOG_MODE == "loki":
        logs = search_loki(service, center_ts, window_minutes, level)
    elif LOG_MODE == "docker":
        logs = fetch_docker_logs(service, since_minutes=window_minutes * 2 + 5)
        # Filter by window
        logs = [
            l for l in logs
            if within_window(l.get("@timestamp", ""), center_ts, window_minutes)
        ]
        if level:
            logs = [l for l in logs if l.get("level", "").upper() == level.upper()]
    else:
        # Default: synthetic store
        logs = search_synthetic(service, center_ts, window_minutes, level)

    logs = logs[:limit]

    # Separate errors and info for Agent 1
    error_logs = [l for l in logs if l.get("level") == "ERROR"]
    info_logs  = [l for l in logs if l.get("level") != "ERROR"]

    return {
        "tool": "search_logs",
        "service": service,
        "alert_timestamp": alert_timestamp or center_ts.isoformat(),
        "window": {
            "start": window_start,
            "end": window_end,
            "minutes_either_side": window_minutes
        },
        "log_mode": LOG_MODE,
        "total_logs_found": len(logs),
        "error_count": len(error_logs),
        "info_count": len(info_logs),
        "logs": logs,
        "agent_instruction": (
            "Call extract_root_cause with these logs to identify the primary error, "
            "then pass the error message to jira-mcp for resolution search."
        )
    }


@app.get("/tools/get_error_summary")
async def get_error_summary(
    service:        str,
    window_minutes: int = 30,
):
    """
    Tool: get_error_summary
    Returns a grouped count of errors by exception_type for a service.
    Useful for Agent 1 to understand error frequency before deep-diving.

    Query params:
      service         required — e.g. 'auth-svc'
      window_minutes  lookback window in minutes (default 30)
    """
    center_ts = datetime.now(timezone.utc)
    logs = search_synthetic(service, center_ts, window_minutes, level_filter="ERROR")

    # Group by exception_type
    summary: dict = {}
    for log in logs:
        exc = log.get("exception_type", "UnknownException")
        if exc not in summary:
            summary[exc] = {
                "exception_type": exc,
                "count": 0,
                "first_seen": log.get("@timestamp"),
                "last_seen":  log.get("@timestamp"),
                "sample_message": log.get("message", "")[:150],
            }
        summary[exc]["count"] += 1
        summary[exc]["last_seen"] = log.get("@timestamp")

    grouped = sorted(summary.values(), key=lambda x: -x["count"])

    return {
        "tool": "get_error_summary",
        "service": service,
        "window_minutes": window_minutes,
        "total_errors": len(logs),
        "unique_exception_types": len(grouped),
        "summary": grouped,
        "queried_at": center_ts.isoformat()
    }


# Maps Prometheus alert names to the exception types they represent.
# When alert_name is provided, extract_root_cause filters logs to only
# return the exception matching that alert — preventing cross-alert confusion.
ALERT_EXCEPTION_MAP = {
    # payment-svc
    "PaymentDBPoolExhausted":      "SQLException",
    "PaymentGatewayTimeouts":      "PaymentGatewayException",
    "PaymentHighErrorRate":        ["DuplicateTransactionException", "InsufficientFundsException"],
    # auth-svc
    "AuthRedisDown":               "RedisConnectionException",
    "AuthBruteForceDetected":      "BruteForceException",
    "AuthHighErrorRate":           ["LDAPTimeoutException", "JWTSignatureException", "TokenRaceConditionException"],
    # loan-svc
    "LoanCreditBureauDown":        "CreditBureauTimeoutException",
    "LoanHighPendingApplications": ["RiskEngineException", "ArithmeticException", "KYCVerificationException", "DocumentParseException"],
    # notification-svc
    "NotificationSMSGatewayDown":  "SMSGatewayException",
    "NotificationQueueOverflow":   "QueueOverflowException",
    "NotificationHighErrorRate":   ["SMTPConnectionException", "TemplateRenderException", "FCMTokenException"],
}


@app.get("/tools/extract_root_cause")
async def extract_root_cause_tool(
    service:         str,
    alert_timestamp: Optional[str] = None,
    window_minutes:  int = 5,
    alert_name:      Optional[str] = None,
):
    """
    Tool: extract_root_cause
    Searches logs and returns the single most likely root-cause error.
    Agent 1 calls this to get a clean error message to pass to jira-mcp.

    Query params:
      service          required — e.g. 'loan-svc'
      alert_timestamp  ISO timestamp from grafana-mcp
      window_minutes   search window (default 5)
    """
    center_ts = parse_timestamp(alert_timestamp) if alert_timestamp \
        else datetime.now(timezone.utc)

    if not center_ts:
        raise HTTPException(status_code=400, detail="Invalid alert_timestamp format")

    if LOG_MODE == "loki":
        logs = search_loki(service, center_ts, window_minutes,
                           alert_name=alert_name)
        if not logs:
            logs = search_loki(service, center_ts, window_minutes * 3,
                               alert_name=alert_name)
    else:
        logs = search_synthetic(service, center_ts, window_minutes, level_filter=None)
        if not logs:
            # Widen window and retry
            logs = search_synthetic(service, center_ts, window_minutes * 3, level_filter=None)

    # Filter logs to match the specific alert if alert_name is provided.
    # This ensures PaymentDBPoolExhausted gets SQLException, not PaymentGatewayException.
    if alert_name and alert_name in ALERT_EXCEPTION_MAP:
        target = ALERT_EXCEPTION_MAP[alert_name]
        targets = [target] if isinstance(target, str) else target
        alert_logs = [l for l in logs if l.get("exception_type") in targets]
        if alert_logs:
            logs = alert_logs  # use filtered set if it has entries

    root_cause = extract_root_cause(logs)
    root_cause["service"] = service
    root_cause["alert_timestamp"] = alert_timestamp or center_ts.isoformat()
    root_cause["logs_searched"] = len(logs)

    if root_cause["found"]:
        root_cause["next_action"] = (
            "Pass 'full_message' or 'exception_type' to "
            "jira-mcp /tools/search_issues for resolution lookup."
        )
    else:
        root_cause["next_action"] = (
            "No ERROR logs found. Try widening window_minutes or check "
            "get_error_summary for recent error patterns."
        )

    return {
        "tool": "extract_root_cause",
        "root_cause": root_cause,
        "queried_at": center_ts.isoformat()
    }


@app.get("/tools/list_tools")
async def list_tools():
    """MCP tool manifest — Agent 1 calls this on startup."""
    return {
        "mcp_server": "kibana-mcp",
        "version": "1.0.0",
        "description": "Provides log search and root-cause extraction for Agent 1",
        "log_mode": LOG_MODE,
        "tools": [
            {
                "name": "search_logs",
                "endpoint": "GET /tools/search_logs",
                "description": "Logs for a service within ±window_minutes of alert timestamp",
                "params": {
                    "service":          "required — service name e.g. payment-svc",
                    "alert_timestamp":  "optional — ISO timestamp from grafana-mcp",
                    "window_minutes":   "optional — window either side in minutes (default 5)",
                    "level":            "optional — ERROR | WARN | INFO",
                    "limit":            "optional — max results (default 50)"
                }
            },
            {
                "name": "get_error_summary",
                "endpoint": "GET /tools/get_error_summary",
                "description": "Grouped error counts by exception type for a service",
                "params": {
                    "service":        "required",
                    "window_minutes": "optional — lookback window (default 30)"
                }
            },
            {
                "name": "extract_root_cause",
                "endpoint": "GET /tools/extract_root_cause",
                "description": "Returns the single most likely root-cause error — ready for jira-mcp",
                "params": {
                    "service":          "required",
                    "alert_timestamp":  "optional — ISO timestamp from grafana-mcp",
                    "window_minutes":   "optional — default 5"
                }
            }
        ]
    }


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT)
