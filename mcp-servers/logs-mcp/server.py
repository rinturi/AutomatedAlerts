"""
logs-mcp — Generic log source MCP server.

Replaces kibana-mcp. Supports any log source via config.yml.
The connector is loaded at startup from config.yml logs.connector.

Supported connectors:
    loki           -- Grafana Loki (default)
    splunk         -- Splunk REST API
    elasticsearch  -- Elasticsearch / OpenSearch
    cloudwatch     -- AWS CloudWatch Logs
    datadog_logs   -- Datadog Logs API

Tools (identical interface to kibana-mcp -- agents unchanged):
    GET /tools/extract_root_cause
    GET /tools/search_logs
    GET /tools/get_error_summary
    GET /health
    GET /tools/list_tools
"""

import os
import sys
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
import uvicorn

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../..")
))

from platform.connector_loader import load_log_connector, _load_config
from platform.config_schema import validate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("logs-mcp")

MCP_PORT = int(os.getenv("MCP_PORT", "9002"))

try:
    cfg            = _load_config()
    validate(cfg)
    connector      = load_log_connector()
    CONNECTOR_NAME = cfg.get("logs", {}).get("connector", "unknown")
    ORG_NAME       = cfg.get("organisation", {}).get("name", "unknown")
    logger.info(f"logs-mcp started — org={ORG_NAME}  connector={CONNECTOR_NAME}")
except Exception as e:
    logger.error(f"Failed to load log connector: {e}")
    raise SystemExit(1)

app = FastAPI(title="logs-mcp", version="2.0.0")


@app.get("/health")
async def health():
    return {
        "status":       "up",
        "service":      "logs-mcp",
        "version":      "2.0.0",
        "connector":    CONNECTOR_NAME,
        "organisation": ORG_NAME,
        "log_mode":     CONNECTOR_NAME,
        "timestamp":    datetime.now(timezone.utc).isoformat(),
    }


@app.get("/tools/extract_root_cause")
async def extract_root_cause(
    service:         str,
    alert_timestamp: Optional[str] = None,
    window_minutes:  int = 5,
    alert_name:      Optional[str] = None,
):
    """
    Returns the most likely root-cause error for a firing alert.
    Agent 1 calls this on cache MISS.
    Interface identical to kibana-mcp — agents need no changes.
    """
    try:
        root_cause = connector.extract_root_cause(
            service          = service,
            alert_name       = alert_name or "",
            alert_timestamp  = alert_timestamp,
            window_minutes   = window_minutes,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Log source error: {e}")

    result = {
        "found":            root_cause.found,
        "service":          root_cause.service,
        "exception_type":   root_cause.exception_type,
        "full_message":     root_cause.full_message,
        "short_error":      root_cause.short_error,
        "timestamp":        root_cause.timestamp,
        "level":            root_cause.level,
        "recurrence_count": root_cause.recurrence_count,
        "source":           root_cause.source,
        "alert_timestamp":  alert_timestamp,
        "logs_searched":    root_cause.recurrence_count,
    }

    if root_cause.found:
        result["next_action"] = (
            "Pass full_message or exception_type to "
            "issues-mcp /tools/search_issues for resolution lookup."
        )
    else:
        result["next_action"] = (
            "No ERROR logs found. Try widening window_minutes."
        )

    return {
        "tool":       "extract_root_cause",
        "connector":  CONNECTOR_NAME,
        "root_cause": result,
        "queried_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/tools/search_logs")
async def search_logs(
    service:         str,
    alert_timestamp: Optional[str] = None,
    window_minutes:  int = 5,
    level:           Optional[str] = None,
    limit:           int = 50,
):
    """
    Returns raw log entries for a service within a time window.
    Interface identical to kibana-mcp — agents need no changes.
    """
    try:
        logs = connector.search_logs(
            service          = service,
            level            = level or "ERROR",
            alert_timestamp  = alert_timestamp,
            window_minutes   = window_minutes,
            limit            = limit,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Log source error: {e}")

    error_logs = [l for l in logs if l.get("level") == "ERROR"]

    return {
        "tool":             "search_logs",
        "connector":        CONNECTOR_NAME,
        "service":          service,
        "alert_timestamp":  alert_timestamp,
        "window_minutes":   window_minutes,
        "total_logs_found": len(logs),
        "error_count":      len(error_logs),
        "logs":             logs,
        "queried_at":       datetime.now(timezone.utc).isoformat(),
    }


@app.get("/tools/get_error_summary")
async def get_error_summary(
    service:        str,
    window_minutes: int = 30,
):
    """Returns grouped error counts by exception type."""
    try:
        summary = connector.get_error_summary(service)
    except Exception:
        summary = {}

    return {
        "tool":       "get_error_summary",
        "connector":  CONNECTOR_NAME,
        "service":    service,
        "summary":    summary,
        "queried_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/tools/list_tools")
async def list_tools():
    return {
        "mcp_server":   "logs-mcp",
        "version":      "2.0.0",
        "connector":    CONNECTOR_NAME,
        "organisation": ORG_NAME,
        "description":  "Generic log source MCP — connector configured via config.yml",
        "tools": [
            {
                "name":        "extract_root_cause",
                "endpoint":    "GET /tools/extract_root_cause",
                "description": "Most likely root-cause error for a firing alert",
                "params": {
                    "service":          "required",
                    "alert_timestamp":  "optional — ISO timestamp",
                    "window_minutes":   "optional — default 5",
                    "alert_name":       "optional — for exception type mapping",
                }
            },
            {
                "name":        "search_logs",
                "endpoint":    "GET /tools/search_logs",
                "description": "Raw log entries within a time window",
                "params": {
                    "service":         "required",
                    "alert_timestamp": "optional",
                    "window_minutes":  "optional — default 5",
                    "level":           "optional — ERROR | WARN | INFO",
                    "limit":           "optional — default 50",
                }
            },
            {
                "name":        "get_error_summary",
                "endpoint":    "GET /tools/get_error_summary",
                "description": "Grouped error counts by exception type",
                "params": {
                    "service":        "required",
                    "window_minutes": "optional — default 30",
                }
            },
        ]
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT)
