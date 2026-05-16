"""
llm_client.py — LLM integration for Agent 2 (JIRA Resolver).

Uses OpenAI GPT-4o-mini to:
  1. Summarise how a matched JIRA resolution applies to the current incident
  2. Generate a contextualised explanation for the L1 engineer
  3. Assess confidence that the resolution actually fits

Called by agent_jira_resolver.py after fetching a resolution from jira-mcp.
"""

import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("llm_client")

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
LLM_MODEL      = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1024"))
LLM_ENABLED    = os.getenv("LLM_ENABLED", "true").lower() == "true"


def _call_openai(prompt: str, max_tokens: int = None) -> Optional[str]:
    """Generic OpenAI chat completion call. Returns response text or None."""
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set — skipping LLM call")
        return None

    try:
        resp = httpx.post(
            OPENAI_API_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model":      LLM_MODEL,
                "max_tokens": max_tokens or LLM_MAX_TOKENS,
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            },
            timeout=30.0
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    except httpx.HTTPStatusError as e:
        logger.error(
            f"OpenAI API HTTP error {e.response.status_code}: "
            f"{e.response.text[:200]}"
        )
        return None
    except Exception as e:
        logger.error(f"OpenAI API call failed: {e}")
        return None


def summarise_resolution(
    service:          str,
    alert_name:       str,
    alert_timestamp:  str,
    error_message:    str,
    error_exception:  str,
    jira_issue_id:    str,
    jira_summary:     str,
    resolution:       str,
    resolution_steps: list,
    vector_score:     float,
) -> Optional[dict]:
    """
    Call GPT-4o-mini to produce a contextualised resolution summary.

    Returns a dict with:
      llm_summary        — 2-3 sentence explanation for the L1 engineer
      llm_confidence     — high / medium / low
      llm_key_action     — single most important step to take first
      llm_risk_note      — any risk or caveat before applying the fix
    """
    if not LLM_ENABLED:
        logger.info("LLM disabled — skipping summarisation")
        return None

    steps_text = "\n".join(
        f"  {i+1}. {s}" for i, s in enumerate(resolution_steps)
    ) if resolution_steps else "  No steps available"

    prompt = f"""You are an expert L1 banking operations engineer assistant.

A monitoring alert has fired and a similar past JIRA resolution has been found.
Analyse whether the past resolution applies to the current incident and provide
a clear, actionable summary for the L1 engineer on duty.

## Current Incident
- Service:     {service}
- Alert:       {alert_name}
- Timestamp:   {alert_timestamp}
- Exception:   {error_exception or 'Unknown'}
- Error:       {error_message}

## Matched Past Resolution (JIRA {jira_issue_id})
- Issue:       {jira_summary}
- Similarity:  {vector_score:.2f} (semantic match score out of 1.0)
- Resolution:  {resolution}
- Steps:
{steps_text}

## Your Task
Respond ONLY with valid JSON in this exact format (no markdown, no extra text):
{{
  "llm_summary": "2-3 sentences explaining how this resolution applies to the current incident. Be specific about what is likely happening right now and why this fix should work.",
  "llm_confidence": "high or medium or low based on how well the past resolution matches this exact error",
  "llm_key_action": "The single most important first step the L1 engineer should take right now",
  "llm_risk_note": "Any risk, caveat, or thing to verify before applying the fix. Write None if no risks."
}}"""

    raw = _call_openai(prompt)
    if not raw:
        return None

    try:
        # Strip markdown code fences if model added them
        cleaned = raw
        if cleaned.startswith("```"):
            parts = cleaned.split("```")
            cleaned = parts[1] if len(parts) > 1 else cleaned
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        cleaned = cleaned.strip()

        result = json.loads(cleaned)
        logger.info(
            f"LLM summary generated — confidence={result.get('llm_confidence')}, "
            f"model={LLM_MODEL}"
        )
        return result

    except json.JSONDecodeError as e:
        logger.error(f"LLM response JSON parse failed: {e}. Raw: {raw[:200]}")
        return None


def generate_new_incident_description(
    service:         str,
    alert_name:      str,
    alert_timestamp: str,
    error_message:   str,
    error_exception: str,
) -> Optional[str]:
    """
    When no JIRA match is found, use GPT-4o-mini to generate a well-structured
    incident description for the new JIRA ticket Agent 2 creates.
    """
    if not LLM_ENABLED or not OPENAI_API_KEY:
        return None

    prompt = f"""You are a banking operations engineer creating a JIRA incident ticket.

Write a clear, structured incident description for the following error.
Keep it under 200 words. Include: what happened, likely impact on customers,
and suggested investigation steps for the on-call engineer.

Service:    {service}
Alert:      {alert_name}
Timestamp:  {alert_timestamp}
Exception:  {error_exception or 'Unknown'}
Error:      {error_message}

Write only the description text. No headings, no JSON, no markdown."""

    result = _call_openai(prompt, max_tokens=512)
    if result:
        logger.info("LLM generated incident description for new JIRA ticket")
    return result
