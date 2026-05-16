"""
agent_alert_monitor.py — Agent 1: Alert Monitor

Responsibilities:
  1. Poll grafana-mcp for firing alerts
  2. For the highest-priority alert, call get_alert_detail
  3. Call kibana-mcp extract_root_cause to get the error message
  4. Populate the IncidentState and hand off to Agent 2

This is a LangGraph node function — it receives state, enriches it, returns updated state.
"""

import logging
import os
import re
from datetime import datetime, timezone

from state import IncidentState
import mcp_client as mcp


def _normalise_timestamp(ts: str) -> str:
    """
    Convert any timestamp format from grafana-mcp to strict ISO 8601.
    grafana-mcp returns: "2026-04-05 05:45:35 UTC"
    kibana-mcp expects:  "2026-04-05T05:45:35+00:00"
    """
    if not ts:
        return ts
    # Already ISO format with T and timezone offset
    if "T" in ts and ("+" in ts or ts.endswith("Z")):
        return ts
    # Format: "2026-04-05 05:45:35 UTC" or "2026-04-05 05:45:35"
    ts_clean = ts.replace(" UTC", "").replace(" Z", "").strip()
    ts_clean = ts_clean.replace(" ", "T")
    if not ts_clean.endswith("+00:00") and not ts_clean.endswith("Z"):
        ts_clean += "+00:00"
    return ts_clean

logger = logging.getLogger("agent_alert_monitor")

# Alert severity priority for picking which to process first
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def run(state: IncidentState) -> IncidentState:
    """
    LangGraph node — Agent 1: Alert Monitor.

    Polls grafana-mcp for the highest-priority firing alert,
    extracts the root cause from kibana-mcp, and returns
    an enriched state for Agent 2.
    """
    logger.info("Agent 1 starting — polling grafana-mcp for alerts")

    state["pipeline_start"] = datetime.now(timezone.utc).isoformat()
    state["retry_count"]    = state.get("retry_count", 0)

    # ── Step 1: Use pre-populated alert from orchestrator (if available) ───────
    # The orchestrator fetches all alerts and pre-fills state for each one.
    # If alert_id is already set, skip fetching the alert list again.
    if state.get("alert_id") and state.get("service"):
        logger.info(
            f"Agent 1: Using pre-populated alert — {state.get('alert_name')} "
            f"on {state.get('service')} [{state.get('alert_severity')}]"
        )
        # Still fetch full detail to get the normalised timestamp
        detail_response = mcp.get_alert_detail(state["alert_id"])
        if detail_response:
            alert_detail = detail_response.get("alert", {})
            state["alert_timestamp"] = _normalise_timestamp(
                alert_detail.get("alert_timestamp") or state.get("alert_timestamp", "")
            )
            state["alert_summary"] = alert_detail.get("summary", state.get("alert_summary", ""))
        else:
            state["alert_timestamp"] = _normalise_timestamp(state.get("alert_timestamp", ""))

    else:
        # ── Fallback: fetch alert list ourselves (original behaviour) ──────────
        alerts_response = mcp.list_active_alerts(limit=20)

        if not alerts_response:
            logger.error("Agent 1: grafana-mcp unreachable or returned no data")
            state["pipeline_error"] = "grafana-mcp unreachable"
            return state

        alerts = alerts_response.get("alerts", [])
        total  = alerts_response.get("total_firing", 0)

        if not alerts:
            logger.info(f"Agent 1: No firing alerts found (total_firing={total})")
            state["pipeline_error"] = "no_active_alerts"
            return state

        logger.info(f"Agent 1: Found {total} firing alert(s)")

        top_alert = alerts[0]
        alert_id  = top_alert.get("alert_id")
        detail_response = mcp.get_alert_detail(alert_id)
        alert_detail = detail_response.get("alert", top_alert) if detail_response else top_alert

        state["alert_id"]        = alert_detail.get("alert_id")
        state["alert_name"]      = alert_detail.get("alert_name")
        state["alert_timestamp"] = _normalise_timestamp(alert_detail.get("alert_timestamp"))
        state["alert_severity"]  = alert_detail.get("severity")
        state["alert_summary"]   = alert_detail.get("summary", "")
        state["service"]         = alert_detail.get("service")

    logger.info(
        f"Agent 1: Alert detail retrieved — service={state['service']}, "
        f"timestamp={state['alert_timestamp']}"
    )

    # ── Step 4: Extract root cause from Kibana logs ────────────────────────────
    service = state["service"]
    if not service:
        logger.error("Agent 1: No service name in alert — cannot query logs")
        state["pipeline_error"] = "no_service_in_alert"
        return state

    # ── Step 2: Check alert-resolution cache (Caching RAG) ───────────────────
    logger.info(
        f"Agent 1: Checking alert-resolution cache for "
        f"{state.get('alert_name')}::{service}"
    )
    cache_response = mcp.search_alert_resolution(
        alert_name=state.get("alert_name", ""),
        service=service,
    )

    if cache_response and cache_response.get("hit"):
        logger.info(
            f"Agent 1: Cache HIT — {state.get('alert_name')}::{service} "
            f"(similarity={cache_response.get('similarity')}, "
            f"occurrences={cache_response.get('occurrence_count')}). "
            f"Skipping kibana-mcp and Agent 2."
        )
        # Populate state from cache — Agent 2 will be skipped
        state["error_exception"]  = cache_response.get("exception_type", "")
        state["error_message"]    = ""  # shown via error_exception — avoid duplication
        state["error_timestamp"]  = state.get("alert_timestamp", "")
        state["error_recurrence"] = cache_response.get("occurrence_count", 1)
        state["jira_issue_id"]    = cache_response.get("jira_issue_id", "")
        state["resolution"]       = cache_response.get("llm_key_action", "") or cache_response.get("resolution", "")
        state["resolution_found"] = bool(cache_response.get("jira_issue_id", ""))
        state["jira_summary"]     = cache_response.get("llm_summary", "")
        state["jira_status"]      = "Done"
        state["llm_summary"]      = cache_response.get("llm_summary", "")
        state["llm_confidence"]   = cache_response.get("llm_confidence", "")
        state["llm_key_action"]   = cache_response.get("llm_key_action", "")
        state["llm_risk_note"]    = cache_response.get("llm_risk_note", "")
        state["vector_match_quality"] = "cache_hit"
        state["vector_top_score"]     = 1.0
        state["cache_hit"]        = True
        return state

    logger.info(
        f"Agent 1: Cache MISS for {state.get('alert_name')}::{service}. "
        "Proceeding with kibana-mcp log extraction."
    )
    state["cache_hit"] = False

    # ── Step 3: Extract root cause from Kibana logs ────────────────────────────
    logger.info(f"Agent 1: Querying kibana-mcp for root cause — service={service}")

    root_cause_response = mcp.extract_root_cause(
        service=service,
        alert_timestamp=state.get("alert_timestamp"),
        window_minutes=5,
        alert_name=state.get("alert_name"),
    )

    if not root_cause_response:
        logger.warning(
            f"Agent 1: kibana-mcp returned no data for service={service}. "
            "Using alert context only — continuing to Agent 2."
        )
        state["error_message"] = (
            f"Alert {state.get('alert_name')} fired for {service} "
            f"at {state.get('alert_timestamp')}. Log query failed — using alert context."
        )
        state["error_exception"] = ""
        state["error_recurrence"] = 0
        return state

    root_cause = root_cause_response.get("root_cause", {})

    if not root_cause.get("found"):
        logger.warning(
            f"Agent 1: No ERROR logs found for {service} in ±5 min window. "
            "Widening search..."
        )
        # Retry with wider window
        root_cause_response = mcp.extract_root_cause(
            service=service,
            alert_timestamp=state.get("alert_timestamp"),
            window_minutes=15,
            alert_name=state.get("alert_name"),
        )
        root_cause = root_cause_response.get("root_cause", {}) if root_cause_response else {}

    # Populate error fields in state
    state["error_message"]    = root_cause.get("full_message", "")
    state["error_exception"]  = root_cause.get("exception_type", "")
    state["error_timestamp"]  = root_cause.get("timestamp", state.get("alert_timestamp"))
    state["error_recurrence"] = root_cause.get("recurrence_count", 0)

    if state["error_message"]:
        logger.info(
            f"Agent 1: Root cause extracted — "
            f"exception={state['error_exception']}, "
            f"recurrence={state['error_recurrence']}"
        )
    else:
        logger.warning(
            f"Agent 1: No error message extracted for {service}. "
            "Agent 2 will proceed with limited context."
        )
        state["error_message"] = (
            f"Alert {state['alert_name']} fired for service {service} "
            f"at {state['alert_timestamp']}. No specific error log found in window."
        )

    logger.info("Agent 1 complete — handing off to Agent 2 (JIRA Resolver)")
    return state
