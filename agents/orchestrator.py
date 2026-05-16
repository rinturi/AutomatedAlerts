"""
orchestrator.py — LangGraph pipeline orchestrating all three agents.

Graph structure:
  START
    → agent_1_alert_monitor
        → [if error] → END
        → [if ok]    → agent_2_jira_resolver
                           → agent_3_dashboard_writer
                               → END

The orchestrator also exposes a polling loop that runs the pipeline
every POLL_INTERVAL_SECONDS, processing one alert per cycle.
"""

import logging
import os
import sys
import time
from datetime import datetime, timezone

from langgraph.graph import StateGraph, START, END

from state import IncidentState
import agent_alert_monitor   as agent1
import agent_jira_resolver   as agent2
import agent_dashboard_writer as agent3
import mcp_client as mcp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout
)
logger = logging.getLogger("orchestrator")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))
MAX_CYCLES    = int(os.getenv("MAX_CYCLES", "0"))  # 0 = run forever


# ── Conditional routing ────────────────────────────────────────────────────────

def route_after_agent1(state: IncidentState) -> str:
    """
    Route after Agent 1.
    Only skip to END when there are genuinely no alerts or grafana is down.
    All other errors (including kibana failures) continue to Agent 2
    so the pipeline still produces a dashboard record.
    """
    hard_stop_errors = ("no_active_alerts", "grafana-mcp unreachable",
                        "no_service_in_alert")
    err = state.get("pipeline_error")
    if err in hard_stop_errors:
        logger.info(f"Orchestrator: Stopping pipeline — {err}")
        return "skip_to_end"
    # service must be set for Agent 2 to do anything useful
    if not state.get("service"):
        logger.warning("Orchestrator: No service in state — skipping to end")
        return "skip_to_end"
    # Cache HIT — skip Agent 2, go directly to Agent 3
    if state.get("cache_hit"):
        logger.info(
            f"Orchestrator: Cache HIT — routing directly to Agent 3 "
            f"(skipping Agent 2 for {state.get('alert_name')})"
        )
        return "cache_hit"
    return "continue"


# ── Build the LangGraph ────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    graph = StateGraph(IncidentState)

    # Add nodes
    graph.add_node("agent_1", agent1.run)
    graph.add_node("agent_2", agent2.run)
    graph.add_node("agent_3", agent3.run)

    # Entry point
    graph.add_edge(START, "agent_1")

    # Conditional edge after Agent 1
    graph.add_conditional_edges(
        "agent_1",
        route_after_agent1,
        {
            "continue":    "agent_2",
            "skip_to_end": END,
            "cache_hit":   "agent_3",
        }
    )

    # Agent 2 always flows to Agent 3
    graph.add_edge("agent_2", "agent_3")

    # Agent 3 is the final node
    graph.add_edge("agent_3", END)

    return graph.compile()


# ── Initial state factory ─────────────────────────────────────────────────────

def empty_state() -> IncidentState:
    return IncidentState(
        alert_id=None, alert_name=None, alert_timestamp=None,
        alert_severity=None, alert_summary=None,
        service=None,
        error_message=None, error_exception=None,
        error_timestamp=None, error_recurrence=None,
        vector_match_quality=None, vector_top_score=None,
        vector_top_issue_id=None,
        jira_issue_id=None, jira_summary=None, jira_status=None,
        resolution=None, resolution_steps=None, resolution_found=None,
        llm_summary=None, llm_confidence=None,
        llm_key_action=None, llm_risk_note=None,
        incident_id=None, dashboard_status=None, remark=None,
        pipeline_start=None, pipeline_error=None, retry_count=0,
        cache_hit=None,
    )


# ── Health check before starting ──────────────────────────────────────────────

def check_readiness():
    logger.info("Orchestrator: Checking all MCP servers...")
    health = mcp.check_all_mcp_servers()
    all_up = True
    for name, result in health.items():
        status = result.get("status")
        if status != "up":
            logger.warning(f"  {name}: {status}")
            all_up = False
        else:
            logger.info(f"  {name}: OK")
    return all_up


# ── Polling loop ──────────────────────────────────────────────────────────────

def run_pipeline_once(graph) -> IncidentState:
    """
    Run one complete pipeline cycle.
    Fetches ALL firing alerts and processes each one individually
    so every service gets a dashboard entry — not just the highest priority.
    """
    logger.info("=" * 60)
    logger.info(f"Orchestrator: Starting pipeline cycle at "
                f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

    # Get the full alert list first
    try:
        alerts_response = mcp.list_active_alerts(limit=20)
    except Exception as e:
        logger.error(f"Orchestrator: Failed to fetch alerts — {e}")
        return empty_state()

    if not alerts_response:
        logger.info("Cycle: grafana-mcp unreachable")
        return empty_state()

    alerts = alerts_response.get("alerts", [])
    if not alerts:
        logger.info("Cycle: No active alerts — nothing to process")
        return empty_state()

    # Deduplicate — keep only one alert per alert_name
    # Alertmanager can return multiple instances of the same alert
    seen = set()
    unique_alerts = []
    for a in alerts:
        key = a.get("alert_name", "unknown")
        if key not in seen:
            seen.add(key)
            unique_alerts.append(a)
    alerts = unique_alerts

    logger.info(f"Orchestrator: {len(alerts)} unique alert(s) firing — processing each one")

    last_state = empty_state()
    for i, alert in enumerate(alerts):
        alert_id = alert.get("alert_id")
        alert_name = alert.get("alert_name", "unknown")
        service = alert.get("service", "unknown")
        severity = alert.get("severity", "unknown")
        logger.info(f"Orchestrator: [{i+1}/{len(alerts)}] {alert_name} on {service} [{severity}]")

        # Create a fresh state pre-populated with this alert
        state = empty_state()
        state["alert_id"]       = alert_id
        state["alert_name"]     = alert_name
        state["service"]        = service
        state["alert_severity"] = severity
        state["alert_summary"]  = alert.get("summary", "")
        state["alert_timestamp"]= alert.get("alert_timestamp", "")

        try:
            final_state = graph.invoke(state)
            _log_cycle_summary(final_state)
            last_state = final_state
        except Exception as e:
            logger.error(f"Orchestrator: Pipeline error on {alert_name} — {e}", exc_info=True)

    return last_state


def _log_cycle_summary(state: IncidentState):
    """Log a clean summary of what happened in this cycle."""
    if state.get("pipeline_error") == "no_active_alerts":
        logger.info("Cycle summary: No alerts firing — nothing to process")
        return

    logger.info("Cycle summary:")
    vq    = state.get("vector_match_quality") or "—"
    vscore = state.get("vector_top_score")
    vscore_str = f"{vscore:.3f}" if vscore is not None else "—"
    logger.info(f"  Alert:       {state.get('alert_name')} [{state.get('alert_severity')}]")
    logger.info(f"  Service:     {state.get('service')}")
    logger.info(f"  Exception:   {state.get('error_exception') or '—'}")
    logger.info(f"  Vector match:{vq} (score={vscore_str})")
    logger.info(f"  JIRA issue:  {state.get('jira_issue_id') or '—'} [{state.get('jira_status') or '—'}]")
    logger.info(f"  Resolution:  {'FOUND' if state.get('resolution_found') else 'NOT FOUND'}")
    llm_conf    = state.get("llm_confidence") or "not generated"
    llm_action  = state.get("llm_key_action") or "—"
    llm_risk    = state.get("llm_risk_note")  or "—"
    llm_summary = state.get("llm_summary")    or ""
    if llm_summary:
        logger.info(f"  LLM summary: confidence={llm_conf}")
        logger.info(f"  LLM action:  {llm_action}")
        logger.info(f"  LLM risk:    {llm_risk}")
        short = llm_summary[:120] + ("..." if len(llm_summary) > 120 else "")
        logger.info(f"  LLM text:    {short}")
    else:
        logger.info(f"  LLM summary: not generated (LLM disabled or no resolution found)")
    logger.info(f"  Dashboard:   {state.get('dashboard_status') or '—'} "
                f"(id={state.get('incident_id') or '—'})")
    if state.get("pipeline_error"):
        logger.warning(f"  Pipeline error: {state.get('pipeline_error')}")


def main():
    logger.info("Orchestrator starting — Banking L1 Agentic AI Pipeline")
    logger.info(f"Poll interval: {POLL_INTERVAL}s | Max cycles: {MAX_CYCLES or 'unlimited'}")

    # Wait for MCP servers to be ready
    for attempt in range(10):
        if check_readiness():
            logger.info("All MCP servers ready — starting polling loop")
            break
        logger.warning(f"Not all MCP servers ready — retrying in 10s (attempt {attempt+1}/10)")
        time.sleep(10)
    else:
        logger.error("MCP servers not available after 10 attempts — exiting")
        sys.exit(1)

    # Build the compiled LangGraph
    graph = build_graph()
    logger.info("LangGraph pipeline compiled successfully")

    # Polling loop
    cycle = 0
    while True:
        cycle += 1
        run_pipeline_once(graph)

        if MAX_CYCLES and cycle >= MAX_CYCLES:
            logger.info(f"Reached MAX_CYCLES={MAX_CYCLES} — stopping")
            break

        logger.info(f"Orchestrator: Next poll in {POLL_INTERVAL}s...")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
