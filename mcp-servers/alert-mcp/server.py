"""
alert-mcp — Generic alert source MCP server.

Replaces grafana-mcp. Supports any alert source via config.yml.
The connector is loaded at startup from config.yml alerts.connector.

Supported connectors (config.yml alerts.connector):
    grafana_alertmanager  -- Grafana Alertmanager (default)
    pagerduty             -- PagerDuty Events API
    datadog               -- Datadog Monitors API
    opsgenie              -- OpsGenie Alerts API
    custom_webhook        -- Generic webhook receiver

Tools exposed (identical interface to grafana-mcp -- agents unchanged):
    GET /tools/list_active_alerts
    GET /tools/get_alert_detail/{alert_id}
    GET /tools/get_alert_history
    GET /health
    GET /tools/list_tools
"""

import os
import sys
import logging
from datetime import datetime, timezone
from typing import Optional
from dataclasses import asdict

from fastapi import FastAPI, HTTPException
import uvicorn

# ── Path setup (allow importing platform/) ────────────────────────────────────
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
))

from platform.connector_loader import load_alert_connector
from platform.config_schema import validate
from platform.connector_loader import _load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("alert-mcp")

# ── Load connector at startup ─────────────────────────────────────────────────
MCP_PORT = int(os.getenv("MCP_PORT", "9001"))

try:
    cfg = _load_config()
    validate(cfg)
    connector = load_alert_connector()
    CONNECTOR_NAME = cfg.get("alerts", {}).get("connector", "unknown")
    ORG_NAME       = cfg.get("organisation", {}).get("name", "unknown")
    logger.info(f"alert-mcp started — org={ORG_NAME}  connector={CONNECTOR_NAME}")
except Exception as e:
    logger.error(f"Failed to load connector: {e}")
    raise SystemExit(1)

app = FastAPI(title="alert-mcp", version="2.0.0")

# ── Helper — serialise Alert dataclass to dict ────────────────────────────────
def alert_to_dict(alert) -> dict:
    return {
        "alert_id":        alert.alert_id,
        "alert_name":      alert.alert_name,
        "service":         alert.service,
        "severity":        alert.severity,
        "alert_timestamp": alert.timestamp,
        "summary":         alert.summary,
        "status":          alert.status,
        "labels":          alert.labels,
        "source":          alert.source,
    }

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status":       "up",
        "service":      "alert-mcp",
        "version":      "2.0.0",
        "connector":    CONNECTOR_NAME,
        "organisation": ORG_NAME,
        "timestamp":    datetime.now(timezone.utc).isoformat(),
    }


@app.get("/tools/list_active_alerts")
async def list_active_alerts(
    severity: Optional[str] = None,
    service:  Optional[str] = None,
    limit:    int = 20,
):
    """
    Returns all currently FIRING alerts from the configured alert source.
    Agent 1 calls this every polling cycle.
    Interface identical to grafana-mcp — agents need no changes.
    """
    try:
        alerts = connector.list_active_alerts(
            severity=severity,
            service=service,
            limit=limit,
        )
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Alert source error: {e}")

    return {
        "tool":            "list_active_alerts",
        "total_firing":    len(alerts),
        "connector":       CONNECTOR_NAME,
        "filters_applied": {"severity": severity, "service": service},
        "alerts":          [alert_to_dict(a) for a in alerts],
        "queried_at":      datetime.now(timezone.utc).isoformat(),
        "agent_instruction": (
            "For each alert call get_alert_detail with alert_id "
            "before querying logs-mcp."
        )
    }


@app.get("/tools/get_alert_detail/{alert_id}")
async def get_alert_detail(alert_id: str):
    """
    Returns full detail for a specific alert.
    Interface identical to grafana-mcp — agents need no changes.
    """
    try:
        alert = connector.get_alert_detail(alert_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    if not alert:
        raise HTTPException(
            status_code=404,
            detail=f"Alert {alert_id} not found. It may have resolved."
        )

    d = alert_to_dict(alert)

    # Preserve kibana_search_hint for Agent 1 compatibility
    d["kibana_search_hint"] = {
        "service_name":     alert.service,
        "alert_timestamp":  alert.timestamp,
        "search_window":    "+-5 minutes from alert_timestamp",
        "suggested_filter": f'service: "{alert.service}" AND level: "ERROR"',
        "next_action":      "Call logs-mcp /tools/extract_root_cause"
    }

    return {
        "tool":       "get_alert_detail",
        "alert":      d,
        "connector":  CONNECTOR_NAME,
        "queried_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/tools/get_alert_history")
async def get_alert_history(limit: int = 10):
    """
    Returns recently resolved alerts.
    Interface identical to grafana-mcp — agents need no changes.
    """
    try:
        alerts = connector.get_alert_history(limit=limit)
    except Exception:
        alerts = []

    return {
        "tool":            "get_alert_history",
        "total_resolved":  len(alerts),
        "resolved_alerts": [alert_to_dict(a) for a in alerts],
        "connector":       CONNECTOR_NAME,
        "queried_at":      datetime.now(timezone.utc).isoformat(),
    }


@app.get("/tools/list_tools")
async def list_tools():
    return {
        "mcp_server":  "alert-mcp",
        "version":     "2.0.0",
        "connector":   CONNECTOR_NAME,
        "organisation": ORG_NAME,
        "description": "Generic alert source MCP — connector configured via config.yml",
        "tools": [
            {
                "name":        "list_active_alerts",
                "endpoint":    "GET /tools/list_active_alerts",
                "description": "Returns all currently firing alerts",
                "params": {
                    "severity": "optional — critical | warning",
                    "service":  "optional — filter by service name",
                    "limit":    "optional — max results (default 20)"
                }
            },
            {
                "name":        "get_alert_detail",
                "endpoint":    "GET /tools/get_alert_detail/{alert_id}",
                "description": "Full detail for one alert",
                "params": {
                    "alert_id": "required — from list_active_alerts"
                }
            },
            {
                "name":        "get_alert_history",
                "endpoint":    "GET /tools/get_alert_history",
                "description": "Recently resolved alerts",
                "params": {
                    "limit": "optional — max results (default 10)"
                }
            }
        ]
    }


# ── Webhook receiver (for connectors that push alerts) ────────────────────────
@app.post("/webhook")
async def webhook_receiver(payload: dict):
    """
    Receives webhook POST from alert sources that push (Alertmanager, PagerDuty).
    Passes to connector if it implements handle_webhook().
    """
    if hasattr(connector, "handle_webhook"):
        connector.handle_webhook(payload)
    return {"status": "received", "connector": CONNECTOR_NAME}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT)
