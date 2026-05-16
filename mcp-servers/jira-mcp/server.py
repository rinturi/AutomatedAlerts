"""
jira-mcp: MCP server that provides JIRA defect search for Agent 2 (JIRA Resolver).

Uses a local JSON store (jira_store.json) as a synthetic JIRA instance.
Supports keyword search and simple token-overlap similarity scoring.
Vector-based semantic search is handled separately by vectordb-mcp.

Tools:
  GET  /tools/search_issues      → keyword search on error message
  GET  /tools/get_issue/{id}     → full detail for one JIRA ticket
  POST /tools/create_issue       → create a new JIRA incident
  GET  /tools/list_tools         → tool manifest
  GET  /health                   → server health
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="jira-mcp", version="1.0.0")

# ── Config ────────────────────────────────────────────────────────────────────
MCP_PORT        = int(os.getenv("MCP_PORT", "9003"))
STORE_PATH      = Path(os.getenv("JIRA_STORE_PATH", "/app/jira_store.json"))

# ── Real Jira Cloud config ────────────────────────────────────────────────────
JIRA_URL       = os.getenv("JIRA_URL", "")
JIRA_EMAIL     = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
JIRA_PROJECT   = os.getenv("JIRA_PROJECT", "KAN")
USE_REAL_JIRA  = bool(JIRA_URL and JIRA_EMAIL and JIRA_API_TOKEN)

import urllib.request as _urllib_req
import urllib.parse   as _urllib_parse
import base64         as _base64

def _jira_headers():
    creds = _base64.b64encode(f"{JIRA_EMAIL}:{JIRA_API_TOKEN}".encode()).decode()
    return {"Authorization": f"Basic {creds}",
            "Content-Type":  "application/json",
            "Accept":        "application/json"}

def _jira_get(path, params=None):
    import json as _json
    url = f"{JIRA_URL}{path}"
    if params:
        url += "?" + _urllib_parse.urlencode(params)
    req = _urllib_req.Request(url, headers=_jira_headers())
    with _urllib_req.urlopen(req, timeout=10) as resp:
        return _json.loads(resp.read())

def _jira_post(path, payload):
    import json as _json
    data = _json.dumps(payload).encode()
    req  = _urllib_req.Request(
        f"{JIRA_URL}{path}", data=data, headers=_jira_headers(), method="POST")
    with _urllib_req.urlopen(req, timeout=10) as resp:
        return _json.loads(resp.read())

def _parse_jira_issue(issue):
    fields   = issue.get("fields", {})
    desc_obj = fields.get("description") or {}
    desc_text = ""
    if isinstance(desc_obj, dict):
        for block in desc_obj.get("content", []):
            for item in block.get("content", []):
                if item.get("type") == "text":
                    desc_text += item.get("text", "")
    labels = fields.get("labels", [])
    exception_type = next((l for l in labels if "Exception" in l or "Error" in l), "")
    service        = next((l for l in labels if l.endswith("-svc")), "")
    return {
        "id":               issue.get("key"),
        "summary":          fields.get("summary", ""),
        "description":      desc_text,
        "status":           fields.get("status", {}).get("name", "Open"),
        "priority":         fields.get("priority", {}).get("name", "Medium"),
        "labels":           labels,
        "exception_type":   exception_type,
        "service":          service,
        "resolution":       desc_text,
        "resolution_steps": [s.strip() for s in desc_text.split(".")
                             if len(s.strip()) > 20][:5],
        "error_keywords":   labels,
        "source":           "jira_cloud",
    }

SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.25"))


# ── Load defect store ─────────────────────────────────────────────────────────

def load_store() -> dict:
    with open(STORE_PATH, "r") as f:
        return json.load(f)


def save_store(data: dict):
    with open(STORE_PATH, "w") as f:
        json.dump(data, f, indent=2)


# ── Similarity scoring ────────────────────────────────────────────────────────

def tokenise(text: str) -> set:
    """Lowercase, strip punctuation, split into tokens."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return set(t for t in text.split() if len(t) > 2)


def similarity_score(query: str, issue: dict) -> float:
    """
    Token-overlap Jaccard-style similarity between query and issue fields.
    Weights: error_keywords > description > summary.
    Returns score 0.0 - 1.0.
    """
    query_tokens = tokenise(query)
    if not query_tokens:
        return 0.0

    # Build weighted token set from issue
    summary_tokens     = tokenise(issue.get("summary", ""))
    desc_tokens        = tokenise(issue.get("description", ""))
    keyword_tokens     = tokenise(" ".join(issue.get("error_keywords", [])))
    exception_tokens   = tokenise(issue.get("exception_type", ""))

    # Weighted intersection
    kw_score   = len(query_tokens & keyword_tokens)   * 3.0
    exc_score  = len(query_tokens & exception_tokens)  * 2.5
    desc_score = len(query_tokens & desc_tokens)       * 1.5
    sum_score  = len(query_tokens & summary_tokens)    * 1.0

    raw = kw_score + exc_score + desc_score + sum_score
    normaliser = len(query_tokens) * 3.0   # max possible weighted score
    return min(1.0, raw / normaliser) if normaliser > 0 else 0.0


def format_issue_summary(issue: dict, score: float = None) -> dict:
    """Return a condensed view of an issue for search results."""
    out = {
        "id":             issue["id"],
        "summary":        issue["summary"],
        "exception_type": issue.get("exception_type", ""),
        "service":        issue.get("service", ""),
        "status":         issue.get("status", ""),
        "priority":       issue.get("priority", ""),
        "created":        issue.get("created", ""),
        "resolved":       issue.get("resolved", ""),
        "resolution_short": (issue.get("resolution", "")[:200] + "...") if issue.get("resolution") else "",
    }
    if score is not None:
        out["similarity_score"] = round(score, 3)
    return out


# ── Request models ─────────────────────────────────────────────────────────────

class CreateIssueRequest(BaseModel):
    summary: str
    description: str
    service: str
    priority: str = "Medium"
    error_message: str = ""
    exception_type: str = ""
    alert_name: str = ""
    alert_timestamp: str = ""


# ── Tool endpoints ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    store = load_store()
    return {
        "status": "up",
        "service": "jira-mcp",
        "jira_project": store.get("project"),
        "total_issues": len(store.get("issues", [])),
        "store_path": str(STORE_PATH),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/tools/search_issues")
async def search_issues(
    query:     str,
    service:   Optional[str] = None,
    min_score: float = 0.0,
    limit:     int = 10,
):
    """Search JIRA issues. Uses real Jira Cloud (agile board) or local JSON store."""
    if USE_REAL_JIRA:
        try:
            # Fetch all issues from agile board (search endpoint is 410 on free tier)
            all_issues = []
            start_at   = 0
            batch_size = 50
            while True:
                data = _jira_get(
                    "/rest/agile/1.0/board/2/issue",
                    {"maxResults": batch_size, "startAt": start_at,
                     "fields": "summary,labels,status,priority,description"}
                )
                batch = data.get("issues", [])
                all_issues.extend(batch)
                if len(batch) < batch_size:
                    break
                start_at += batch_size

            # Filter and score in memory
            query_lower = query.lower()
            query_words = set(query_lower.split())
            scored = []
            for issue in all_issues:
                fields  = issue.get("fields", {})
                labels  = [l.lower() for l in fields.get("labels", [])]
                summary = fields.get("summary", "").lower()

                # Skip if service filter does not match
                if service and service.lower() not in labels:
                    continue

                # Score: label match = 3pts, summary word match = 1pt each
                score = 0.0
                for word in query_words:
                    if word.lower() in labels:
                        score += 3.0
                    if word.lower() in summary:
                        score += 1.0

                if score >= min_score:
                    scored.append((score, issue))

            scored.sort(key=lambda x: -x[0])
            top = scored[:limit]
            issues = [_parse_jira_issue(i) for _, i in top]
            quality = "none"
            if top:
                quality = "high" if top[0][0] >= 3 else "medium"
            return {
                "tool":          "search_issues",
                "query":         query,
                "match_quality": quality,
                "total_found":   len(issues),
                "issues":        issues,
                "source":        "jira_cloud",
            }
        except Exception as e:
            print(f"Jira Cloud search_issues failed: {e} — falling back to local store")

    # Fallback to local JSON store
    store  = load_store()
    issues = store.get("issues", [])
    query_lower = query.lower()
    scored = []
    for issue in issues:
        score = 0.0
        text  = f"{issue.get('summary','')} {issue.get('description','')} {' '.join(issue.get('error_keywords',[]))}".lower()
        if service and issue.get("service") != service:
            continue
        for word in query_lower.split():
            if word in text:
                score += 1.0
        if score > 0:
            scored.append((score, issue))
    scored.sort(key=lambda x: -x[0])
    results = [i for score, i in scored[:limit] if score >= min_score]
    quality = "high" if results and scored[0][0] >= 3 else ("medium" if results else "none")
    return {
        "tool":          "search_issues",
        "query":         query,
        "match_quality": quality,
        "total_found":   len(results),
        "issues":        results,
        "source":        "local_json",
    }

@app.get("/tools/get_issue/{issue_id}")
async def get_issue(issue_id: str):
    """Get full issue detail — uses real Jira Cloud API or local JSON store."""
    if USE_REAL_JIRA:
        try:
            data  = _jira_get(
                f"/rest/api/3/issue/{issue_id}",
                {"fields": "summary,labels,status,priority,description"}
            )
            issue = _parse_jira_issue(data)
            return {"tool": "get_issue", "issue": issue, "source": "jira_cloud"}
        except Exception as e:
            print(f"Jira Cloud get_issue failed: {e} — falling back to local store")

    # Fallback to local JSON store
    store = load_store()
    for issue in store.get("issues", []):
        if issue.get("id") == issue_id:
            return {"tool": "get_issue", "issue": issue, "source": "local_json"}
    return {"tool": "get_issue", "issue": None, "error": f"{issue_id} not found"}

@app.post("/tools/create_issue")
async def create_issue(req: CreateIssueRequest):
    """Create a new JIRA issue — uses real Jira Cloud API or local JSON store."""
    if USE_REAL_JIRA:
        try:
            payload = {
                "fields": {
                    "project":     {"key": JIRA_PROJECT},
                    "summary":     req.summary,
                    "description": {
                        "type": "doc", "version": 1,
                        "content": [{"type": "paragraph", "content": [
                            {"type": "text",
                             "text": req.description or req.summary}
                        ]}]
                    },
                    "issuetype": {"name": "Bug"},
                    "labels":    [l for l in [
                                    req.service,
                                    req.exception_type or "UnknownException"
                                  ] if l],
                    "priority":  {"name": "High" if req.priority == "critical"
                                          else "Medium"},
                }
            }
            data = _jira_post("/rest/api/3/issue", payload)
            key  = data.get("key")
            print(f"Created real Jira issue: {key}")
            return {
                "tool":     "create_issue",
                "issue_id": key,
                "url":      f"{JIRA_URL}/browse/{key}",
                "status":   "created",
                "source":   "jira_cloud",
            }
        except Exception as e:
            print(f"Jira Cloud create_issue failed: {e} — falling back to local store")

    # Fallback to local JSON store
    store    = load_store()
    issue_id = f"KAN-{len(store.get('issues', [])) + 1001}"
    new_issue = {
        "id":               issue_id,
        "summary":          req.summary,
        "description":      req.description or req.summary,
        "status":           "Open",
        "priority":         req.priority or "Medium",
        "service":          req.service,
        "exception_type":   req.exception_type or "",
        "labels":           [req.service],
        "resolution":       "",
        "resolution_steps": [],
        "error_keywords":   [req.exception_type or ""],
        "source":           "local_json",
    }
    store.setdefault("issues", []).append(new_issue)
    with open(STORE_PATH, "w") as f:
        json.dump(store, f, indent=2)
    return {
        "tool":     "create_issue",
        "issue_id": issue_id,
        "status":   "created",
        "source":   "local_json",
    }

@app.get("/tools/list_issues")
async def list_issues(
    service:  Optional[str] = None,
    status:   Optional[str] = None,
    limit:    int = 20,
):
    """
    List all issues with optional filters. Useful for agent context building.
    """
    store = load_store()
    issues = store.get("issues", [])

    if service:
        issues = [i for i in issues if service.lower() in i.get("service", "").lower()]
    if status:
        issues = [i for i in issues if status.lower() == i.get("status", "").lower()]

    issues = issues[:limit]

    return {
        "tool": "list_issues",
        "total": len(issues),
        "filters": {"service": service, "status": status},
        "issues": [format_issue_summary(i) for i in issues],
        "queried_at": datetime.now(timezone.utc).isoformat()
    }


@app.get("/tools/list_tools")
async def list_tools():
    """MCP tool manifest — Agent 2 calls this on startup."""
    return {
        "mcp_server": "jira-mcp",
        "version": "1.0.0",
        "description": "Searches JIRA defects and creates new incidents for Agent 2",
        "tools": [
            {
                "name": "search_issues",
                "endpoint": "GET /tools/search_issues",
                "description": "Keyword similarity search on JIRA defects using error message",
                "params": {
                    "query":     "required — error message or exception type from kibana-mcp",
                    "service":   "optional — filter by service name",
                    "status":    "optional — filter by status e.g. Done",
                    "min_score": "optional — similarity threshold (default 0.25)",
                    "limit":     "optional — max results (default 5)"
                }
            },
            {
                "name": "get_issue",
                "endpoint": "GET /tools/get_issue/{issue_id}",
                "description": "Full issue detail including resolution steps",
                "params": {
                    "issue_id": "required — e.g. BANK-1001 from search_issues results"
                }
            },
            {
                "name": "create_issue",
                "endpoint": "POST /tools/create_issue",
                "description": "Create new JIRA incident when no match found",
                "params": {
                    "summary":         "required",
                    "description":     "required",
                    "service":         "required",
                    "priority":        "optional — default Medium",
                    "error_message":   "optional",
                    "exception_type":  "optional",
                    "alert_name":      "optional",
                    "alert_timestamp": "optional"
                }
            }
        ]
    }


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT)
