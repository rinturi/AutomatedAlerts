"""
ai_provider.py — Universal LLM adapter using LiteLLM.

Reads config.yml llm block and routes to the correct provider.
Supports: OpenAI, Anthropic, Azure OpenAI, AWS Bedrock, Ollama.

Usage:
    from ai_provider import call_llm
    response = call_llm(prompt="Your prompt here")
"""

import json
import logging
import os
import sys
from typing import Optional

import yaml

logger = logging.getLogger("ai_provider")

# ── Load config.yml ───────────────────────────────────────────────────────────
CONFIG_PATH = os.getenv("CONFIG_PATH", "/app/config.yml")

def _load_llm_config() -> dict:
    """Load LLM config from config.yml, fall back to env vars."""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        return cfg.get("llm", {})
    # Fallback: legacy env var mode (banking-mock compatibility)
    return {
        "provider": "openai",
        "model":    os.getenv("LLM_MODEL", "gpt-4o-mini"),
        "config":   {"api_key": os.getenv("OPENAI_API_KEY", "")}
    }

LLM_CFG     = _load_llm_config()
LLM_ENABLED = os.getenv("LLM_ENABLED", "true").lower() == "true"
MAX_TOKENS  = int(os.getenv("LLM_MAX_TOKENS", str(
    LLM_CFG.get("config", {}).get("max_tokens", 1024)
)))


def _resolve_env(value: str) -> str:
    """Resolve ${ENV_VAR} placeholders in config values."""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        env_key = value[2:-1]
        return os.getenv(env_key, "")
    return value


def _build_litellm_params() -> dict:
    """
    Build the parameters dict for litellm.completion() based on config.yml.

    Handles all supported providers:
        openai       -> model="gpt-4o-mini"
        anthropic    -> model="anthropic/claude-haiku-4-5"
        azure_openai -> model="azure/gpt-4o-mini"
        bedrock      -> model="bedrock/anthropic.claude-3-haiku-20240307-v1:0"
        ollama       -> model="ollama/llama3.1:8b"
    """
    provider   = LLM_CFG.get("provider", "openai")
    model      = LLM_CFG.get("model", "gpt-4o-mini")
    cfg        = LLM_CFG.get("config", {})

    # Resolve ${ENV_VAR} placeholders
    cfg = {k: _resolve_env(v) for k, v in cfg.items()}

    params = {"max_tokens": MAX_TOKENS}

    if provider == "openai":
        params["model"]   = model
        params["api_key"] = cfg.get("api_key", os.getenv("OPENAI_API_KEY", ""))

    elif provider == "anthropic":
        params["model"]   = f"anthropic/{model}"
        params["api_key"] = cfg.get("api_key", os.getenv("ANTHROPIC_API_KEY", ""))

    elif provider == "azure_openai":
        params["model"]           = f"azure/{cfg.get('deployment', model)}"
        params["api_key"]         = cfg.get("api_key", os.getenv("AZURE_OPENAI_API_KEY", ""))
        params["api_base"]        = cfg.get("endpoint", "")
        params["api_version"]     = cfg.get("api_version", "2024-02-01")

    elif provider == "bedrock":
        params["model"]                  = f"bedrock/{model}"
        params["aws_access_key_id"]      = cfg.get("aws_access_key", os.getenv("AWS_ACCESS_KEY_ID", ""))
        params["aws_secret_access_key"]  = cfg.get("aws_secret_key", os.getenv("AWS_SECRET_ACCESS_KEY", ""))
        params["aws_region_name"]        = cfg.get("region", os.getenv("AWS_REGION", "us-east-1"))

    elif provider == "ollama":
        base_url          = cfg.get("url", "http://ollama:11434")
        params["model"]   = f"ollama/{model}"
        params["api_base"]= base_url

    else:
        raise ValueError(
            f"Unknown LLM provider: '{provider}'. "
            f"Supported: openai, anthropic, azure_openai, bedrock, ollama"
        )

    logger.info(f"LLM provider={provider}  model={params['model']}")
    return params


def call_llm(prompt: str, max_tokens: Optional[int] = None) -> Optional[str]:
    """
    Send a prompt to the configured LLM and return the response text.

    Args:
        prompt:     The full prompt string to send
        max_tokens: Override max tokens (uses config default if None)

    Returns:
        Response text string, or None on any failure.
        Never raises — failures are logged and return None gracefully.
    """
    if not LLM_ENABLED:
        logger.info("LLM disabled (LLM_ENABLED=false) — skipping call")
        return None

    try:
        import litellm
        litellm.suppress_debug_info = True

        params = _build_litellm_params()
        if max_tokens:
            params["max_tokens"] = max_tokens

        params["messages"] = [{"role": "user", "content": prompt}]

        response = litellm.completion(**params)
        text = response.choices[0].message.content.strip()

        provider = LLM_CFG.get("provider", "openai")
        model    = LLM_CFG.get("model", "gpt-4o-mini")
        logger.info(f"LLM response received — provider={provider} model={model}")
        return text

    except ImportError:
        logger.error("litellm not installed — run: pip install litellm")
        return _fallback_openai(prompt, max_tokens)

    except Exception as e:
        logger.error(f"LLM call failed ({LLM_CFG.get('provider')}): {e}")
        return None


def _fallback_openai(prompt: str, max_tokens: Optional[int] = None) -> Optional[str]:
    """
    Direct OpenAI urllib fallback if litellm is unavailable.
    Preserves backward compatibility with banking-mock.
    """
    import urllib.request
    api_key = os.getenv("OPENAI_API_KEY", "")
    model   = os.getenv("LLM_MODEL", "gpt-4o-mini")

    if not api_key:
        logger.warning("OPENAI_API_KEY not set — cannot call LLM")
        return None

    payload = json.dumps({
        "model":      model,
        "max_tokens": max_tokens or MAX_TOKENS,
        "messages":   [{"role": "user", "content": prompt}]
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Fallback OpenAI call failed: {e}")
        return None


def get_provider_info() -> dict:
    """Return current LLM provider info — used by health endpoints."""
    provider = LLM_CFG.get("provider", "openai")
    model    = LLM_CFG.get("model", "gpt-4o-mini")
    return {
        "provider":    provider,
        "model":       model,
        "enabled":     LLM_ENABLED,
        "max_tokens":  MAX_TOKENS,
        "config_path": CONFIG_PATH,
    }
