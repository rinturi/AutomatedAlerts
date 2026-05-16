"""
agent_jira_resolver.py — Agent 2: JIRA Resolver

Responsibilities:
  1. Call vectordb-mcp for semantic similarity search on the error message
  2. Based on match_quality, call jira-mcp to get resolution or create incident
  3. If new issue created, index it in vectordb-mcp for future searches
  4. Populate resolution fields in state and hand off to Agent 3

Decision flow:
  vectordb-mcp embed_and_search
    → high (>= 0.75)   → jira-mcp get_issue                   → resolution found
    → medium (0.50-0.75)→ jira-mcp get_issue + search_issues   → verify resolution
    → low / none       → jira-mcp search_issues (keyword)
                           → match found  → get_issue           → resolution found
                           → no match     → create_issue         → new incident
"""

import logging
from datetime import datetime, timezone

from state import IncidentState
import mcp_client as mcp
import llm_client as llm

logger = logging.getLogger("agent_jira_resolver")

VECTOR_HIGH_THRESHOLD   = 0.75
VECTOR_MEDIUM_THRESHOLD = 0.50


def _fetch_resolution(issue_id: str, state: IncidentState) -> bool:
    """
    Fetch full issue detail from jira-mcp and populate resolution fields.
    Returns True if resolution was successfully retrieved.
    NOTE: LLM call is done in run() after this returns, so LangGraph
    can see all state mutations in the top-level node function.
    """
    logger.info(f"Agent 2: Fetching resolution for {issue_id}")
    issue_response = mcp.get_jira_issue(issue_id)

    if not issue_response:
        logger.error(f"Agent 2: Failed to fetch issue {issue_id} from jira-mcp")
        return False

    issue = issue_response.get("issue", {})

    state["jira_issue_id"]    = issue.get("id")
    state["jira_summary"]     = issue.get("summary")
    state["jira_status"]      = issue.get("status")
    state["resolution"]       = issue.get("resolution")
    state["resolution_steps"] = issue.get("resolution_steps", [])
    state["resolution_found"] = bool(issue.get("resolution"))

    if state["resolution_found"]:
        logger.info(
            f"Agent 2: Resolution found in {issue_id} — "
            f"'{state['jira_summary'][:60]}...'"
        )
    else:
        logger.warning(f"Agent 2: Issue {issue_id} has no resolution text")

    return True



def _call_llm(state: IncidentState) -> None:
    """
    Call GPT-4o-mini to contextualise the resolution for the current incident.
    Writes llm_summary, llm_confidence, llm_key_action, llm_risk_note
    directly into state from within the LangGraph node run() scope.
    """
    logger.info("Agent 2: Calling LLM to contextualise resolution...")
    llm_result = llm.summarise_resolution(
        service          = state.get("service", ""),
        alert_name       = state.get("alert_name", ""),
        alert_timestamp  = state.get("alert_timestamp", ""),
        error_message    = state.get("error_message", ""),
        error_exception  = state.get("error_exception", ""),
        jira_issue_id    = state.get("jira_issue_id", ""),
        jira_summary     = state.get("jira_summary", ""),
        resolution       = state.get("resolution", ""),
        resolution_steps = state.get("resolution_steps", []),
        vector_score     = state.get("vector_top_score") or 0.0,
    )
    if llm_result:
        state["llm_summary"]    = llm_result.get("llm_summary")
        state["llm_confidence"] = llm_result.get("llm_confidence")
        state["llm_key_action"] = llm_result.get("llm_key_action")
        state["llm_risk_note"]  = llm_result.get("llm_risk_note")
        logger.info(f"Agent 2: LLM summary ready — confidence={state.get('llm_confidence')}")
    else:
        logger.warning("Agent 2: LLM unavailable — proceeding without summary")
        state["llm_summary"]    = None
        state["llm_confidence"] = None
        state["llm_key_action"] = None
        state["llm_risk_note"]  = None


def run(state: IncidentState) -> IncidentState:
    """
    LangGraph node — Agent 2: JIRA Resolver.

    Uses vectordb-mcp (semantic) then jira-mcp (keyword) to find
    a resolution for the error extracted by Agent 1.
    """
    logger.info("Agent 2 starting — resolving error via vectordb + jira")

    error_message   = state.get("error_message", "")
    error_exception = state.get("error_exception", "")
    service         = state.get("service", "")

    if not error_message:
        logger.error("Agent 2: No error message in state — cannot search")
        state["pipeline_error"] = "no_error_message_from_agent1"
        state["resolution_found"] = False
        return state

    # Build the search query — combine exception type and message for best results
    search_query = f"{error_exception} {error_message}".strip()
    if len(search_query) > 500:
        search_query = search_query[:500]

    logger.info(
        f"Agent 2: Searching for — exception={error_exception}, "
        f"service={service}"
    )

    # ── Step 1: Semantic search via vectordb-mcp ───────────────────────────────
    logger.info("Agent 2: Step 1 — semantic search via vectordb-mcp")

    vector_response = mcp.embed_and_search(
        query=search_query,
        service_filter=service,
        top_k=5,
    )

    match_quality = "none"
    top_score     = 0.0
    top_issue_id  = None

    if vector_response:
        match_quality = vector_response.get("match_quality", "none")
        results       = vector_response.get("results", [])
        if results:
            top_score    = results[0].get("similarity_score", 0.0)
            top_issue_id = results[0].get("issue_id")

        logger.info(
            f"Agent 2: Vector search result — quality={match_quality}, "
            f"top_score={top_score:.3f}, top_issue={top_issue_id}"
        )
    else:
        logger.warning("Agent 2: vectordb-mcp unreachable — falling through to keyword search")

    state["vector_match_quality"] = match_quality
    state["vector_top_score"]     = top_score
    state["vector_top_issue_id"]  = top_issue_id

    # ── Step 2: High confidence — fetch resolution directly ────────────────────
    if match_quality == "high" and top_issue_id:
        logger.info(
            f"Agent 2: High confidence match ({top_score:.3f}) — "
            f"fetching resolution from {top_issue_id}"
        )
        success = _fetch_resolution(top_issue_id, state)
        if success and state.get("resolution_found"):
            _call_llm(state)
            logger.info("Agent 2 complete — resolution found via semantic search")
            return state

    # ── Step 3: Medium confidence — verify with jira keyword search too ────────
    if match_quality == "medium" and top_issue_id:
        logger.info(
            f"Agent 2: Medium confidence ({top_score:.3f}) — "
            f"verifying with jira-mcp keyword search"
        )
        keyword_response = mcp.search_jira_issues(
            query=search_query,
            service=service,
            limit=3
        )

        if keyword_response and keyword_response.get("results"):
            keyword_top_id    = keyword_response["results"][0].get("id")
            keyword_quality   = keyword_response.get("match_quality", "none")
            logger.info(
                f"Agent 2: Keyword match — quality={keyword_quality}, "
                f"id={keyword_top_id}"
            )
            # Use whichever has higher confidence
            use_id = top_issue_id if top_score >= 0.60 else (keyword_top_id or top_issue_id)
        else:
            use_id = top_issue_id

        success = _fetch_resolution(use_id, state)
        if success and state.get("resolution_found"):
            _call_llm(state)
            logger.info("Agent 2 complete — resolution found via medium match + verification")
            return state

    # ── Step 4: Low / no semantic match — try keyword search in jira-mcp ──────
    logger.info("Agent 2: Step 4 — keyword search via jira-mcp")

    keyword_response = mcp.search_jira_issues(
        query=search_query,
        service=service,
        limit=5
    )

    if keyword_response:
        kw_quality = keyword_response.get("match_quality", "none")
        kw_results = keyword_response.get("results", [])
        logger.info(
            f"Agent 2: Keyword search result — quality={kw_quality}, "
            f"matches={len(kw_results)}"
        )

        if kw_quality in ("high", "medium") and kw_results:
            kw_issue_id = kw_results[0].get("id")
            success = _fetch_resolution(kw_issue_id, state)
            if success and state.get("resolution_found"):
                _call_llm(state)
                logger.info("Agent 2 complete — resolution found via keyword search")
                return state

    # ── Step 5: No match anywhere — create new JIRA incident ──────────────────
    logger.info(
        "Agent 2: No matching resolution found — creating new JIRA incident"
    )

    # Use LLM to generate a better incident description if available
    auto_description = llm.generate_new_incident_description(
        service         = service,
        alert_name      = state.get("alert_name", ""),
        alert_timestamp = state.get("alert_timestamp", ""),
        error_message   = error_message,
        error_exception = error_exception,
    ) or (
        f"Alert: {state.get('alert_name')}\n"
        f"Service: {service}\n"
        f"Alert timestamp: {state.get('alert_timestamp')}\n"
        f"Error message: {error_message}\n"
        f"Exception type: {error_exception}\n"
        f"Recurrence count: {state.get('error_recurrence', 0)}\n\n"
        f"This incident was automatically created by the Agentic AI L1 system "
        f"because no matching resolution was found in the knowledge base."
    )

    create_response = mcp.create_jira_issue(
        summary     = f"[AUTO] {error_exception or 'Unknown error'} in {service}",
        description = auto_description,
        service         = service,
        priority        = "High" if state.get("alert_severity") == "critical" else "Medium",
        error_message   = error_message,
        exception_type  = error_exception,
        alert_name      = state.get("alert_name", ""),
        alert_timestamp = state.get("alert_timestamp", ""),
    )

    if create_response:
        new_issue = create_response.get("issue", {})
        new_id    = create_response.get("issue_id") or new_issue.get("id")

        state["jira_issue_id"]    = new_id
        state["jira_summary"]     = new_issue.get("summary")
        state["jira_status"]      = "Open"
        state["resolution"]       = None
        state["resolution_steps"] = []
        state["resolution_found"] = False
        state["remark"]           = "No similar error recorded — new incident created"

        logger.info(f"Agent 2: New incident created — {new_id}")

        # Index the new issue in vectordb-mcp for future semantic searches
        index_text = f"{error_exception} {error_message} {service}"
        mcp.index_document(
            doc_id         = new_id,
            text           = index_text,
            issue_id       = new_id,
            summary        = new_issue.get("summary", ""),
            exception_type = error_exception,
            service        = service,
            status         = "Open",
        )
        logger.info(f"Agent 2: New issue {new_id} indexed in vectordb-mcp")

    else:
        logger.error("Agent 2: Failed to create JIRA issue")
        state["resolution_found"] = False
        state["remark"]           = "No similar error recorded — incident creation failed"
        state["pipeline_error"]   = "jira_create_issue_failed"

    logger.info("Agent 2 complete — handing off to Agent 3 (Dashboard Writer)")
    return state
