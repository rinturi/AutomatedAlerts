"""
mcp_client.py — Thin HTTP client for all four MCP servers.

All agents import this module. It provides one function per MCP tool,
handling timeouts, error logging, and response parsing consistently.
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("mcp_client")

# ── MCP server base URLs (from environment, defaults use Docker network names)
GRAFANA_MCP_URL  = os.getenv("GRAFANA_MCP_URL",  "http://alert-mcp:9001")
KIBANA_MCP_URL   = os.getenv("KIBANA_MCP_URL",   "http://kibana-mcp:9002")
JIRA_MCP_URL     = os.getenv("JIRA_MCP_URL",     "http://jira-mcp:9003")
VECTORDB_MCP_URL = os.getenv("VECTORDB_MCP_URL", "http://vectordb-mcp:9004")
ALERT_RES_URL    = os.getenv("ALERT_RESOLUTION_MCP_URL", "http://alert-resolution-mcp:9005")

TIMEOUT = float(os.getenv("MCP_TIMEOUT", "30"))


# ── Generic HTTP helper ────────────────────────────────────────────────────────

def _get(url: str, params: dict = None) -> Optional[dict]:
    try:
        resp = httpx.get(url, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error {e.response.status_code} calling {url}: {e.response.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"Error calling {url}: {e}")
        return None


def _post(url: str, body: dict) -> Optional[dict]:
    try:
        resp = httpx.post(url, json=body, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error {e.response.status_code} calling {url}: {e.response.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"Error calling {url}: {e}")
        return None


# ── grafana-mcp tools ─────────────────────────────────────────────────────────

def list_active_alerts(severity: str = None, service: str = None, limit: int = 10) -> Optional[dict]:
    """grafana-mcp: Get all currently firing alerts."""
    params = {"limit": limit}
    if severity:
        params["severity"] = severity
    if service:
        params["service"] = service
    return _get(f"{GRAFANA_MCP_URL}/tools/list_active_alerts", params)


def get_alert_detail(alert_id: str) -> Optional[dict]:
    """grafana-mcp: Get full detail for one alert including kibana_search_hint."""
    return _get(f"{GRAFANA_MCP_URL}/tools/get_alert_detail/{alert_id}")


# ── kibana-mcp tools ──────────────────────────────────────────────────────────

def extract_root_cause(service: str, alert_timestamp: str = None,
                       window_minutes: int = 5, alert_name: str = None) -> Optional[dict]:
    """kibana-mcp: Extract the most likely root-cause error for a service."""
    params = {"service": service, "window_minutes": window_minutes}
    if alert_timestamp:
        params["alert_timestamp"] = alert_timestamp
    if alert_name:
        params["alert_name"] = alert_name
    return _get(f"{KIBANA_MCP_URL}/tools/extract_root_cause", params)


def search_logs(service: str, alert_timestamp: str = None, level: str = "ERROR", limit: int = 20) -> Optional[dict]:
    """kibana-mcp: Search logs for a service in a time window."""
    params = {"service": service, "level": level, "limit": limit}
    if alert_timestamp:
        params["alert_timestamp"] = alert_timestamp
    return _get(f"{KIBANA_MCP_URL}/tools/search_logs", params)


# ── vectordb-mcp tools ────────────────────────────────────────────────────────

def embed_and_search(query: str, service_filter: str = None, top_k: int = 5) -> Optional[dict]:
    """vectordb-mcp: Semantic similarity search for similar past defects."""
    params = {"query": query, "top_k": top_k}
    if service_filter:
        params["service_filter"] = service_filter
    return _get(f"{VECTORDB_MCP_URL}/tools/embed_and_search", params)


def index_document(doc_id: str, text: str, issue_id: str = None,
                   summary: str = None, exception_type: str = None,
                   service: str = None, status: str = None) -> Optional[dict]:
    """vectordb-mcp: Index a new document into ChromaDB."""
    return _post(f"{VECTORDB_MCP_URL}/tools/index_document", {
        "doc_id":         doc_id,
        "text":           text,
        "issue_id":       issue_id or doc_id,
        "summary":        summary or "",
        "exception_type": exception_type or "",
        "service":        service or "",
        "status":         status or "",
    })


# ── jira-mcp tools ───────────────────────────────────────────────────────────

def search_jira_issues(query: str, service: str = None, min_score: float = 0.25, limit: int = 5) -> Optional[dict]:
    """jira-mcp: Keyword similarity search on JIRA defect store."""
    params = {"query": query, "limit": limit, "min_score": min_score}
    if service:
        params["service"] = service
    return _get(f"{JIRA_MCP_URL}/tools/search_issues", params)


def get_jira_issue(issue_id: str) -> Optional[dict]:
    """jira-mcp: Get full issue detail including resolution steps."""
    return _get(f"{JIRA_MCP_URL}/tools/get_issue/{issue_id}")


def create_jira_issue(summary: str, description: str, service: str,
                      priority: str = "Medium", error_message: str = "",
                      exception_type: str = "", alert_name: str = "",
                      alert_timestamp: str = "") -> Optional[dict]:
    """jira-mcp: Create a new JIRA incident."""
    return _post(f"{JIRA_MCP_URL}/tools/create_issue", {
        "summary":         summary,
        "description":     description,
        "service":         service,
        "priority":        priority,
        "error_message":   error_message,
        "exception_type":  exception_type,
        "alert_name":      alert_name,
        "alert_timestamp": alert_timestamp,
    })


# ── Health checks ─────────────────────────────────────────────────────────────

def search_alert_resolution(alert_name: str, service: str,
                              threshold: float = 0.75) -> Optional[dict]:
    """Agent 1 — check alert-resolution cache before running full pipeline."""
    return _get(f"{ALERT_RES_URL}/tools/search_alert_resolution", {
        "alert_name": alert_name,
        "service":    service,
        "threshold":  threshold,
    })

def index_alert_resolution(alert_name: str, service: str,
                            exception_type: str = "",
                            resolution: str = "",
                            jira_issue_id: str = "",
                            llm_summary: str = "",
                            llm_confidence: str = "",
                            llm_key_action: str = "",
                            llm_risk_note: str = "") -> Optional[dict]:
    """Agent 3 — populate alert-resolution cache after successful pipeline."""
    return _post(f"{ALERT_RES_URL}/tools/index_alert_resolution", {
        "alert_name":     alert_name,
        "service":        service,
        "exception_type": exception_type,
        "resolution":     resolution,
        "jira_issue_id":  jira_issue_id,
        "llm_summary":    llm_summary,
        "llm_confidence": llm_confidence,
        "llm_key_action": llm_key_action,
        "llm_risk_note":  llm_risk_note,
    })

def check_all_mcp_servers() -> dict:
    """Ping all four MCP servers and return their health status."""
    results = {}
    for name, url in [
        ("grafana-mcp",   GRAFANA_MCP_URL),
        ("kibana-mcp",    KIBANA_MCP_URL),
        ("jira-mcp",      JIRA_MCP_URL),
        ("vectordb-mcp",  VECTORDB_MCP_URL),
    ]:
        try:
            resp = httpx.get(f"{url}/health", timeout=5)
            data = resp.json()
            results[name] = {
                "status": data.get("status", "unknown"),
                "url":    url,
            }
        except Exception as e:
            results[name] = {"status": "unreachable", "error": str(e), "url": url}
    return results
