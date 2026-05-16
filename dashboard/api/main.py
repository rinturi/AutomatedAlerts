"""
dashboard/api/main.py — FastAPI incident store.

Agents write incidents here. The L1 React dashboard reads from here.

Endpoints:
  POST /incidents          → Agent 3 writes a new incident
  GET  /incidents          → L1 dashboard polls this every 10s
  GET  /incidents/{id}     → get one incident by ID
  GET  /health             → health check
"""

import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="L1 Dashboard API", version="1.0.0")

# Allow the React frontend to call this API from the browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory store — last 100 incidents (newest first)
_incidents: deque = deque(maxlen=100)


class Incident(BaseModel):
    # Alert context
    alert_id:        Optional[str] = None
    alert_name:      Optional[str] = None
    alert_severity:  Optional[str] = None
    alert_summary:   Optional[str] = None
    alert_timestamp: Optional[str] = None

    # Service
    service: Optional[str] = None

    # Root cause
    error_message:    Optional[str] = None
    error_exception:  Optional[str] = None
    error_timestamp:  Optional[str] = None
    error_recurrence: Optional[int] = 0

    # Resolution
    resolution_found: Optional[bool] = False
    jira_issue_id:    Optional[str]  = None
    jira_summary:     Optional[str]  = None
    jira_status:      Optional[str]  = None
    resolution:       Optional[str]  = None
    resolution_steps: Optional[list] = []

    # L1 guidance
    remark: Optional[str] = None

    # LLM contextualisation
    llm_summary:    Optional[str] = None
    llm_confidence: Optional[str] = None
    llm_key_action: Optional[str] = None
    llm_risk_note:  Optional[str] = None

    # Metadata
    vector_match_quality: Optional[str]   = None
    vector_top_score:     Optional[float] = None
    pipeline_start:       Optional[str]   = None
    written_at:           Optional[str]   = None
    pipeline_error:       Optional[str]   = None


@app.get("/firing-alerts")
async def get_firing_alerts():
    """
    Proxy endpoint — fetches currently firing alerts from grafana-mcp
    and returns them to the browser. Avoids browser needing direct
    access to grafana-mcp on port 9001.
    """
    import httpx, os
    grafana_mcp = os.getenv("GRAFANA_MCP_URL", "http://grafana-mcp:9001")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{grafana_mcp}/tools/list_active_alerts?limit=50")
            return resp.json()
    except Exception as e:
        return {"alerts": [], "total_firing": 0, "error": str(e)}


@app.get("/health")
def health():
    return {
        "status": "up",
        "service": "dashboard-api",
        "total_incidents": len(_incidents),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.post("/incidents", status_code=201)
def create_incident(incident: Incident):
    incident_id = str(uuid.uuid4())[:8].upper()
    record = incident.model_dump()
    record["incident_id"] = incident_id
    record["received_at"] = datetime.now(timezone.utc).isoformat()

    # Remove any existing incident for the same alert_name + service
    # so the dashboard always shows the LATEST incident per alert, not duplicates
    alert_key = f"{record.get('alert_name')}::{record.get('service')}"
    global _incidents
    _incidents = deque(
        [i for i in _incidents
         if f"{i.get('alert_name')}::{i.get('service')}" != alert_key],
        maxlen=100
    )

    _incidents.appendleft(record)
    return {"incident_id": incident_id, "status": "written"}


@app.get("/incidents")
def list_incidents(limit: int = 50):
    items = list(_incidents)[:limit]
    return {
        "total": len(_incidents),
        "incidents": items
    }


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: str):
    match = next((i for i in _incidents if i.get("incident_id") == incident_id), None)
    if not match:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    return match


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
