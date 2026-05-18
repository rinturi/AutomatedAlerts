"""
issues-mcp — Generic issue tracker MCP server.

Replaces jira-mcp. Supports any issue tracker via config.yml.
The connector is loaded at startup from config.yml issues.connector.

Supported connectors:
    jira_cloud    -- Jira Cloud REST API v3 (default)
    servicenow    -- ServiceNow Table API
    linear        -- Linear GraphQL API
    github_issues -- GitHub REST API
    azure_devops  -- Azure DevOps Work Items

Tools (identical interface to jira-mcp -- agents unchanged):
    GET  /tools/search_issues
    GET  /tools/get_issue/{issue_id}
    POST /tools/create_issue
    GET  /tools/list_issues
    GET  /health
    GET  /tools/list_tools
"""

import os
import sys
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../..")
))

from platform.connector_loader import load_issue_connector, _load_config
from platform.config_schema import validate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("issues-mcp")

MCP_PORT = int(os.getenv("MCP_PORT", "9003"))

try:
    cfg            = _load_config()
    validate(cfg)
    connector      = load_issue_connector()
    CONNECTOR_NAME = cfg.get("issues", {}).get("connector", "unknown")
    ORG_NAME       = cfg.get("organisation", {}).get("name", "unknown")
    logger.info(f"issues-mcp started — org={ORG_NAME}  connector={CONNECTOR_NAME}")
except Exception as e:
    logger.error(f"Failed to load issue connector: {e}")
    raise SystemExit(1)

app = FastAPI(title="issues-mcp", version="2.0.0")


class CreateIssueRequest(BaseModel):
    summary:         str
    description:     str
    service:         str
    priority:        str = "High"
    error_message:   str = ""
    exception_type:  str = ""
    alert_name:      str = ""
    alert_timestamp: str = ""


def issue_to_dict(issue) -> dict:
    return {
        "id":               issue.issue_id,
        "summary":          issue.summary,
        "description":      issue.description,
        "resolution":       issue.resolution,
        "resolution_steps": issue.resolution_steps,
        "status":           issue.status,
        "priority":         issue.priority,
        "labels":           issue.labels,
        "service":          issue.service,
        "exception_type":   issue.exception_type,
        "source":           issue.source,
    }


@app.get("/health")
async def health():
    return {
        "status":       "up",
        "service":      "issues-mcp",
        "version":      "2.0.0",
        "connector":    CONNECTOR_NAME,
        "organisation": ORG_NAME,
        "timestamp":    datetime.now(timezone.utc).isoformat(),
    }


@app.get("/tools/search_issues")
async def search_issues(
    query:     str,
    service:   Optional[str] = None,
    min_score: float = 0.25,
    limit:     int = 10,
):
    """
    Search for existing issues matching an error description.
    Interface identical to jira-mcp — agents need no changes.
    """
    try:
        issues = connector.search_issues(
            query     = query,
            service   = service,
            min_score = min_score,
            limit     = limit,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Issue tracker error: {e}")

    quality = "none"
    if issues:
        quality = "high" if len(issues) > 0 else "medium"

    return {
        "tool":          "search_issues",
        "connector":     CONNECTOR_NAME,
        "query":         query,
        "match_quality": quality,
        "total_found":   len(issues),
        "issues":        [issue_to_dict(i) for i in issues],
        "queried_at":    datetime.now(timezone.utc).isoformat(),
    }


@app.get("/tools/get_issue/{issue_id}")
async def get_issue(issue_id: str):
    """
    Returns full detail for a specific issue.
    Interface identical to jira-mcp — agents need no changes.
    """
    try:
        issue = connector.get_issue(issue_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    if not issue:
        return {"tool": "get_issue", "issue": None,
                "error": f"{issue_id} not found"}

    return {
        "tool":      "get_issue",
        "connector": CONNECTOR_NAME,
        "issue":     issue_to_dict(issue),
        "source":    issue.source,
    }


@app.post("/tools/create_issue")
async def create_issue(req: CreateIssueRequest):
    """
    Create a new issue in the configured tracker.
    Interface identical to jira-mcp — agents need no changes.
    """
    labels = [req.service]
    if req.exception_type:
        labels.append(req.exception_type)

    try:
        created = connector.create_issue(
            summary     = req.summary,
            description = req.description,
            service     = req.service,
            priority    = req.priority,
            labels      = labels,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Issue creation failed: {e}")

    return {
        "tool":      "create_issue",
        "connector": CONNECTOR_NAME,
        "issue_id":  created.issue_id,
        "url":       created.url,
        "status":    "created",
        "source":    created.source,
    }


@app.get("/tools/list_issues")
async def list_issues(
    service: Optional[str] = None,
    status:  Optional[str] = None,
    limit:   int = 20,
):
    """List issues with optional filters."""
    try:
        issues = connector.list_issues(
            service = service,
            status  = status,
            limit   = limit,
        )
    except Exception:
        issues = []

    return {
        "tool":      "list_issues",
        "connector": CONNECTOR_NAME,
        "total":     len(issues),
        "issues":    [issue_to_dict(i) for i in issues],
        "queried_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/tools/list_tools")
async def list_tools():
    return {
        "mcp_server":   "issues-mcp",
        "version":      "2.0.0",
        "connector":    CONNECTOR_NAME,
        "organisation": ORG_NAME,
        "description":  "Generic issue tracker MCP — connector configured via config.yml",
        "tools": [
            {
                "name":        "search_issues",
                "endpoint":    "GET /tools/search_issues",
                "description": "Search for existing issues matching an error",
                "params": {
                    "query":     "required — error message or exception type",
                    "service":   "optional — filter by service name",
                    "min_score": "optional — default 0.25",
                    "limit":     "optional — default 10",
                }
            },
            {
                "name":        "get_issue",
                "endpoint":    "GET /tools/get_issue/{issue_id}",
                "description": "Full issue detail including resolution steps",
                "params": {"issue_id": "required"}
            },
            {
                "name":        "create_issue",
                "endpoint":    "POST /tools/create_issue",
                "description": "Create new issue when no match found",
                "params": {
                    "summary":        "required",
                    "description":    "required",
                    "service":        "required",
                    "priority":       "optional — default High",
                    "exception_type": "optional",
                }
            },
            {
                "name":        "list_issues",
                "endpoint":    "GET /tools/list_issues",
                "description": "List issues with optional filters",
                "params": {
                    "service": "optional",
                    "status":  "optional",
                    "limit":   "optional — default 20",
                }
            },
        ]
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT)
