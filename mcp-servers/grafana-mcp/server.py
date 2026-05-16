"""
grafana-mcp: MCP server that wraps Alertmanager API.
Exposes clean tool endpoints for Agent 1 (Alert Monitor).

Tools:
  GET  /tools/list_active_alerts          → all currently FIRING alerts
  GET  /tools/get_alert_detail/{alert_id} → full detail for one alert
  GET  /tools/get_alert_history           → recently resolved alerts
  GET  /health                            → server health
"""

import hashlib
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="grafana-mcp", version="1.0.0")

# ── Config ────────────────────────────────────────────────────────────────────
ALERTMANAGER_URL = os.getenv("ALERTMANAGER_URL", "http://alertmanager:9093")
PROMETHEUS_URL   = os.getenv("PROMETHEUS_URL",   "http://prometheus:9090")
MCP_PORT         = int(os.getenv("MCP_PORT", "9001"))

# Severity priority for sorting
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_alert_id(alert: dict) -> str:
    """Stable ID derived from alert labels so Agent 1 can reference it."""
    key = str(sorted(alert.get("labels", {}).items()))
    return hashlib.md5(key.encode()).hexdigest()[:12]


def parse_alert(raw: dict) -> dict:
    """Normalise a raw Alertmanager alert into a clean agent-friendly structure."""
    labels      = raw.get("labels", {})
    annotations = raw.get("annotations", {})
    status      = raw.get("status", {})

    starts_at = raw.get("startsAt", "")
    try:
        ts = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
        alert_timestamp = ts.strftime("%Y-%m-%d %H:%M:%S UTC")
        age_seconds = int((datetime.now(timezone.utc) - ts).total_seconds())
    except Exception:
        alert_timestamp = starts_at
        age_seconds = -1

    return {
        "alert_id":        make_alert_id(raw),
        "alert_name":      labels.get("alertname", "unknown"),
        "service":         labels.get("service", labels.get("job", "unknown")),
        "severity":        labels.get("severity", "unknown"),
        "state":           status.get("state", "unknown"),
        "alert_timestamp": alert_timestamp,
        "age_seconds":     age_seconds,
        "summary":         annotations.get("summary", ""),
        "description":     annotations.get("description", ""),
        "labels":          labels,
        "annotations":     annotations,
        "generator_url":   raw.get("generatorURL", ""),
        "fingerprint":     raw.get("fingerprint", make_alert_id(raw)),
    }


async def fetch_alertmanager_alerts(state_filter: str = "active") -> list:
    """Fetch alerts from Alertmanager REST API."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{ALERTMANAGER_URL}/api/v2/alerts",
                params={"active": "true", "silenced": "false", "inhibited": "false"}
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot reach Alertmanager at {ALERTMANAGER_URL}. "
                   "Ensure alertmanager container is running."
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Alertmanager error: {str(e)}")


async def fetch_prometheus_alerts() -> list:
    """Fetch FIRING alerts from Prometheus rules API (richer rule metadata)."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{PROMETHEUS_URL}/api/v1/alerts")
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", {}).get("alerts", [])
    except Exception:
        return []


# ── Tool endpoints ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check — also verifies Alertmanager connectivity."""
    am_status = "unreachable"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{ALERTMANAGER_URL}/-/healthy")
            am_status = "healthy" if r.status_code == 200 else "unhealthy"
    except Exception:
        pass

    return {
        "status": "up",
        "service": "grafana-mcp",
        "alertmanager": am_status,
        "alertmanager_url": ALERTMANAGER_URL,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/tools/list_active_alerts")
async def list_active_alerts(
    severity: Optional[str] = None,
    service:  Optional[str] = None,
    limit:    int = 20
):
    """
    Tool: list_active_alerts
    Returns all currently FIRING alerts from Alertmanager.
    Agent 1 calls this first on every polling cycle.

    Query params:
      severity  filter by 'critical' or 'warning'
      service   filter by service name e.g. 'payment-svc'
      limit     max alerts to return (default 20)
    """
    raw_alerts = await fetch_alertmanager_alerts()

    alerts = [parse_alert(a) for a in raw_alerts]

    # Apply filters
    if severity:
        alerts = [a for a in alerts if a["severity"].lower() == severity.lower()]
    if service:
        alerts = [a for a in alerts if service.lower() in a["service"].lower()]

    # Sort by severity then age (oldest first)
    alerts.sort(key=lambda a: (
        SEVERITY_ORDER.get(a["severity"], 99),
        -a["age_seconds"]
    ))

    alerts = alerts[:limit]

    return {
        "tool": "list_active_alerts",
        "total_firing": len(alerts),
        "filters_applied": {"severity": severity, "service": service},
        "alerts": alerts,
        "queried_at": datetime.now(timezone.utc).isoformat(),
        "agent_instruction": (
            "For each alert, call get_alert_detail with the alert_id to get "
            "full context before querying Kibana logs."
        )
    }


@app.get("/tools/get_alert_detail/{alert_id}")
async def get_alert_detail(alert_id: str):
    """
    Tool: get_alert_detail
    Returns full detail for a specific alert including Prometheus rule metadata.
    Agent 1 uses this to extract the service name and timestamp
    before calling kibana-mcp to fetch logs.

    Path param:
      alert_id  the alert_id from list_active_alerts response
    """
    raw_alerts = await fetch_alertmanager_alerts()
    alerts = [parse_alert(a) for a in raw_alerts]

    match = next((a for a in alerts if a["alert_id"] == alert_id), None)
    if not match:
        raise HTTPException(
            status_code=404,
            detail=f"Alert {alert_id} not found. It may have resolved. "
                   "Call list_active_alerts to get current alert IDs."
        )

    # Enrich with Prometheus rule data
    prom_alerts = await fetch_prometheus_alerts()
    prom_match = next(
        (p for p in prom_alerts
         if p.get("labels", {}).get("alertname") == match["alert_name"]
         and p.get("labels", {}).get("service") == match["service"]),
        None
    )

    if prom_match:
        match["prometheus_value"]      = prom_match.get("value", "")
        match["prometheus_state"]      = prom_match.get("state", "")
        match["active_at"]             = prom_match.get("activeAt", "")

    # Add Kibana search instruction for Agent 1
    match["kibana_search_hint"] = {
        "service_name":     match["service"],
        "alert_timestamp":  match["alert_timestamp"],
        "search_window":    "±5 minutes from alert_timestamp",
        "suggested_filter": f'service: "{match["service"]}" AND level: "ERROR"',
        "next_action":      "Call kibana-mcp /tools/search_logs with these parameters"
    }

    return {
        "tool": "get_alert_detail",
        "alert": match,
        "queried_at": datetime.now(timezone.utc).isoformat()
    }


@app.get("/tools/get_alert_history")
async def get_alert_history(limit: int = 10):
    """
    Tool: get_alert_history
    Returns recently resolved alerts from Alertmanager.
    Useful for Agent 1 to check if an alert is recurring.

    Query params:
      limit  max resolved alerts to return (default 10)
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{ALERTMANAGER_URL}/api/v2/alerts",
                params={"active": "false", "silenced": "true", "inhibited": "true"}
            )
            resp.raise_for_status()
            raw = resp.json()
    except Exception:
        raw = []

    resolved = [parse_alert(a) for a in raw if a.get("status", {}).get("state") != "active"]
    resolved = resolved[:limit]

    return {
        "tool": "get_alert_history",
        "total_resolved": len(resolved),
        "resolved_alerts": resolved,
        "queried_at": datetime.now(timezone.utc).isoformat()
    }


@app.get("/tools/list_tools")
async def list_tools():
    """MCP tool manifest — Agent 1 calls this on startup to discover available tools."""
    return {
        "mcp_server": "grafana-mcp",
        "version": "1.0.0",
        "description": "Wraps Alertmanager API to provide alert data to Agent 1",
        "tools": [
            {
                "name": "list_active_alerts",
                "endpoint": "GET /tools/list_active_alerts",
                "description": "Returns all currently firing alerts",
                "params": {
                    "severity": "optional — filter by critical/warning",
                    "service":  "optional — filter by service name",
                    "limit":    "optional — max results (default 20)"
                }
            },
            {
                "name": "get_alert_detail",
                "endpoint": "GET /tools/get_alert_detail/{alert_id}",
                "description": "Full detail for one alert including Kibana search hint",
                "params": {
                    "alert_id": "required — from list_active_alerts response"
                }
            },
            {
                "name": "get_alert_history",
                "endpoint": "GET /tools/get_alert_history",
                "description": "Recently resolved alerts for recurrence detection",
                "params": {
                    "limit": "optional — max results (default 10)"
                }
            }
        ]
    }


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT)
