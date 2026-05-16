"""
state.py — Shared state schema for the LangGraph agent pipeline.

This TypedDict is the single data structure that flows through
all three agents. LangGraph passes it from node to node,
each agent reading from and writing to their own fields.

Flow:
  Agent 1 (Alert Monitor)    → populates: alert_*, service, error_*
  Agent 2 (JIRA Resolver)    → populates: jira_*, resolution_*, match_*
  Agent 3 (Dashboard Writer) → populates: incident_id, dashboard_status
"""

from typing import Optional, TypedDict


class IncidentState(TypedDict):
    # ── Agent 1 fields — Alert Monitor ────────────────────────────────────────
    alert_id:          Optional[str]   # grafana-mcp alert_id
    alert_name:        Optional[str]   # e.g. PaymentGatewayTimeouts
    alert_timestamp:   Optional[str]   # ISO timestamp from Alertmanager
    alert_severity:    Optional[str]   # critical | warning
    alert_summary:     Optional[str]   # human-readable alert description

    service:           Optional[str]   # affected microservice e.g. payment-svc

    error_message:     Optional[str]   # full error message from kibana-mcp
    error_exception:   Optional[str]   # exception class e.g. PaymentGatewayException
    error_timestamp:   Optional[str]   # timestamp of the log entry
    error_recurrence:  Optional[int]   # how many times seen in window

    # ── Agent 2 fields — JIRA Resolver ────────────────────────────────────────
    vector_match_quality:  Optional[str]   # high | medium | low | none
    vector_top_score:      Optional[float] # cosine similarity score 0-1
    vector_top_issue_id:   Optional[str]   # top matching JIRA issue ID

    jira_issue_id:     Optional[str]   # matched or created JIRA issue ID
    jira_summary:      Optional[str]   # JIRA issue title
    jira_status:       Optional[str]   # Done | Open
    resolution:        Optional[str]   # full resolution text from JIRA
    resolution_steps:  Optional[list]  # list of step strings
    resolution_found:  Optional[bool]  # True if existing resolution found

    # ── Agent 3 fields — Dashboard Writer ─────────────────────────────────────
    incident_id:       Optional[str]   # UUID for the dashboard record
    dashboard_status:  Optional[str]   # written | error
    remark:            Optional[str]   # shown when no resolution found

    # ── LLM fields — populated by Agent 2 after resolution fetch ─────────────
    llm_summary:       Optional[str]   # LLM contextualised explanation for L1 engineer
    llm_confidence:    Optional[str]   # high | medium | low
    llm_key_action:    Optional[str]   # single most important first step
    llm_risk_note:     Optional[str]   # risk or caveat before applying fix

    # ── Pipeline metadata ──────────────────────────────────────────────────────
    pipeline_start:    Optional[str]   # ISO timestamp when pipeline started
    pipeline_error:    Optional[str]   # error message if any agent fails
    cache_hit:         Optional[bool]  # True if Agent 1 found resolution in cache
    retry_count:       Optional[int]   # number of retries attempted
