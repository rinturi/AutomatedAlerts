"""
llm_client.py — LLM integration for Agent 2 (JIRA Resolver).

Now routes through ai_provider.py which supports:
    OpenAI, Anthropic, Azure OpenAI, AWS Bedrock, Ollama
All configured via config.yml llm block.

Public interface unchanged — agents call the same functions.
"""

import json
import logging
from typing import Optional

logger = logging.getLogger("llm_client")

# Import provider — falls back gracefully if config not found
try:
    from ai_provider import call_llm, get_provider_info
    _info = get_provider_info()
    logger.info(
        f"LLM ready — provider={_info['provider']}  "
        f"model={_info['model']}  enabled={_info['enabled']}"
    )
except Exception as e:
    logger.warning(f"ai_provider load warning: {e} — will use env var fallback")
    def call_llm(prompt, max_tokens=None):
        import os, urllib.request
        api_key = os.getenv("OPENAI_API_KEY", "")
        model   = os.getenv("LLM_MODEL", "gpt-4o-mini")
        if not api_key:
            return None
        payload = json.dumps({
            "model": model, "max_tokens": max_tokens or 1024,
            "messages": [{"role": "user", "content": prompt}]
        }).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())["choices"][0]["message"]["content"].strip()
        except Exception:
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
    Produce a contextualised resolution summary for the L1 engineer.
    Routes to configured LLM provider via ai_provider.call_llm().

    Returns dict with: llm_summary, llm_confidence, llm_key_action, llm_risk_note
    Returns None on any failure — pipeline continues without LLM output.
    """
    steps_text = "\n".join(
        f"  {i+1}. {s}" for i, s in enumerate(resolution_steps)
    ) if resolution_steps else "  No steps available"

    prompt = f"""You are an expert L1 banking operations engineer assistant.

A monitoring alert has fired and a similar past issue resolution has been found.
Analyse whether the past resolution applies to the current incident and provide
a clear, actionable summary for the L1 engineer on duty.

## Current Incident
- Service:     {service}
- Alert:       {alert_name}
- Timestamp:   {alert_timestamp}
- Exception:   {error_exception or 'Unknown'}
- Error:       {error_message}

## Matched Past Resolution ({jira_issue_id})
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

    raw = call_llm(prompt)
    if not raw:
        return None

    try:
        cleaned = raw
        if cleaned.startswith("```"):
            parts   = cleaned.split("```")
            cleaned = parts[1] if len(parts) > 1 else cleaned
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        cleaned = cleaned.strip()

        result = json.loads(cleaned)
        logger.info(
            f"LLM summary generated — "
            f"confidence={result.get('llm_confidence')}"
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
    Generate a structured incident description for a new issue ticket.
    Called by Agent 2 when no matching issue is found.
    """
    prompt = f"""You are a banking operations engineer creating an incident ticket.

Write a clear, structured incident description for the following error.
Keep it under 200 words. Include: what happened, likely customer impact,
and suggested investigation steps for the on-call engineer.

Service:    {service}
Alert:      {alert_name}
Timestamp:  {alert_timestamp}
Exception:  {error_exception or 'Unknown'}
Error:      {error_message}

Write only the description text. No headings, no JSON, no markdown."""

    result = call_llm(prompt, max_tokens=512)
    if result:
        logger.info("LLM generated incident description for new issue ticket")
    return result
