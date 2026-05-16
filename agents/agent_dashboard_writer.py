"""
agent_dashboard_writer.py — Agent 3: Dashboard Writer

Responsibilities:
  1. Receive the fully populated IncidentState from Agent 2
  2. Compose a clean incident record
  3. POST it to the dashboard API (FastAPI incident store)
  4. The L1 team reads the dashboard and decides:
       - Apply the resolution steps
       - Create a new JIRA ticket (if remark = no similar error)

This is the final node in the LangGraph pipeline.
"""

import logging
import os
from datetime import datetime, timezone

import httpx
import mcp_client as mcp

from state import IncidentState

logger = logging.getLogger("agent_dashboard_writer")

DASHBOARD_API_URL = os.getenv("DASHBOARD_API_URL", "http://dashboard-api:8080")
TIMEOUT = float(os.getenv("MCP_TIMEOUT", "15"))


def run(state: IncidentState) -> IncidentState:
    """
    LangGraph node — Agent 3: Dashboard Writer.

    Composes the final incident record and writes it to the dashboard API.
    """
    logger.info("Agent 3 starting — writing incident to dashboard")
    logger.info(f"Agent 3 DEBUG — llm_summary={state.get('llm_summary')!r}")
    logger.info(f"Agent 3 DEBUG — llm_confidence={state.get('llm_confidence')!r}")
    logger.info(f"Agent 3 DEBUG — resolution_found={state.get('resolution_found')!r}")

    resolution_found = state.get("resolution_found", False)

    # ── Compose incident record ────────────────────────────────────────────────
    incident = {
        # Alert context
        "alert_id":        state.get("alert_id"),
        "alert_name":      state.get("alert_name"),
        "alert_severity":  state.get("alert_severity"),
        "alert_summary":   state.get("alert_summary"),
        "alert_timestamp": state.get("alert_timestamp"),

        # Affected service
        "service": state.get("service"),

        # Root cause
        "error_message":   state.get("error_message"),
        "error_exception": state.get("error_exception"),
        "error_timestamp": state.get("error_timestamp"),
        "error_recurrence": state.get("error_recurrence", 0),

        # Resolution
        "resolution_found": resolution_found,
        "jira_issue_id":    state.get("jira_issue_id"),
        "jira_summary":     state.get("jira_summary"),
        "jira_status":      state.get("jira_status"),
        "resolution":       state.get("resolution") if resolution_found else None,
        "resolution_steps": state.get("resolution_steps", []) if resolution_found else [],

        # L1 action guidance
        "remark": state.get("remark") or (
            "Resolution found — review and apply resolution steps below"
            if resolution_found
            else "No similar error recorded — investigate and create JIRA incident"
        ),

        # LLM contextualisation
        "llm_summary":    state.get("llm_summary"),
        "llm_confidence": state.get("llm_confidence"),
        "llm_key_action": state.get("llm_key_action"),
        "llm_risk_note":  state.get("llm_risk_note"),

        # Search metadata
        "vector_match_quality": state.get("vector_match_quality"),
        "vector_top_score":     state.get("vector_top_score"),

        # Pipeline metadata
        "pipeline_start":    state.get("pipeline_start"),
        "written_at":        datetime.now(timezone.utc).isoformat(),
        "pipeline_error":    state.get("pipeline_error"),
    }

    # ── POST to dashboard API ──────────────────────────────────────────────────
    try:
        resp = httpx.post(
            f"{DASHBOARD_API_URL}/incidents",
            json=incident,
            timeout=TIMEOUT
        )
        resp.raise_for_status()
        result = resp.json()

        state["incident_id"]      = result.get("incident_id")
        state["dashboard_status"] = "written"

        logger.info(
            f"Agent 3: Incident written — id={state['incident_id']}, "
            f"service={state.get('service')}, "
            f"resolution_found={resolution_found}"
        )


        # ── Populate alert-resolution cache (only on cache MISS) ───────────────
        if not state.get("cache_hit") and state.get("alert_name") and state.get("service"):
            try:
                # Store llm_key_action as resolution — clean, actionable, not full Jira doc
                resolution_to_cache = state.get("llm_key_action", "") or state.get("error_exception", "")
                cache_result = mcp.index_alert_resolution(
                    alert_name    = state.get("alert_name", ""),
                    service       = state.get("service", ""),
                    exception_type= state.get("error_exception", ""),
                    resolution    = resolution_to_cache,
                    jira_issue_id = state.get("jira_issue_id", ""),
                    llm_summary   = state.get("llm_summary", ""),
                    llm_confidence= state.get("llm_confidence", ""),
                    llm_key_action= state.get("llm_key_action", ""),
                    llm_risk_note = state.get("llm_risk_note", ""),
                )
                if cache_result:
                    logger.info(
                        f"Agent 3: Alert resolution cached — "
                        f"{state.get('alert_name')}::{state.get('service')} "
                        f"action={cache_result.get('action')} "
                        f"occurrences={cache_result.get('occurrence_count')}"
                    )
                else:
                    logger.warning("Agent 3: Failed to cache alert resolution")
            except Exception as cache_err:
                logger.warning(f"Agent 3: Cache population error (non-fatal): {cache_err}")

    except httpx.ConnectError:
        logger.error(f"Agent 3: Dashboard API unreachable at {DASHBOARD_API_URL}")
        state["dashboard_status"] = "error"
        state["pipeline_error"]   = "dashboard_api_unreachable"

    except Exception as e:
        logger.error(f"Agent 3: Failed to write incident — {e}")
        state["dashboard_status"] = "error"
        state["pipeline_error"]   = f"dashboard_write_error: {e}"

    logger.info("Agent 3 complete — pipeline finished")
    return state
