"""
embed_client.py — Universal embedding adapter.

Reads config.yml embeddings block and routes to the correct provider.
Used by vectordb-mcp and alert-resolution-mcp instead of calling
OpenAI directly.

Supported providers (config.yml embeddings.provider):
    openai       -- OpenAI text-embedding-3-small / large (default)
    cohere       -- Cohere embed-english-v3.0
    ollama       -- Ollama nomic-embed-text (local, free)
    huggingface  -- HuggingFace sentence-transformers (local, free)

Usage:
    from embed_client import get_embedding, get_embeddings, get_dimensions
    vector  = get_embedding("PaymentGatewayException timeout")
    vectors = get_embeddings(["text1", "text2"])
    dims    = get_dimensions()
"""

import logging
import os
import sys
from typing import List, Optional

import yaml

logger = logging.getLogger("embed_client")

# ── Load config ───────────────────────────────────────────────────────────────
CONFIG_PATH = os.getenv("CONFIG_PATH", "/app/config.yml")

def _load_embed_config() -> dict:
    """Load embeddings config from config.yml, fall back to env vars."""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        embed_cfg = cfg.get("embeddings", {})
        # Inject model into config block for providers
        if "model" not in embed_cfg.get("config", {}):
            embed_cfg.setdefault("config", {})["model"] = embed_cfg.get("model", "text-embedding-3-small")
        return embed_cfg

    # Fallback: legacy env var mode (banking-mock compatibility)
    return {
        "provider": "openai",
        "model":    os.getenv("EMBED_MODEL", "text-embedding-3-small"),
        "config":   {"api_key": os.getenv("OPENAI_API_KEY", "")}
    }

EMBED_CFG = _load_embed_config()


def _load_provider():
    """Instantiate the correct embedding provider from config."""
    provider   = EMBED_CFG.get("provider", "openai")
    cfg        = EMBED_CFG.get("config", {}).copy()

    # Always pass model to provider config
    cfg["model"] = EMBED_CFG.get("model", cfg.get("model", "text-embedding-3-small"))

    # Add platform/ to path for imports
    platform_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )
    if platform_path not in sys.path:
        sys.path.insert(0, platform_path)

    logger.info(f"Loading embedding provider: {provider}  model: {cfg.get('model')}")

    if provider == "openai":
        from platform.connectors.embeddings.openai_embed import OpenAIEmbeddingProvider
        return OpenAIEmbeddingProvider(cfg)

    elif provider == "cohere":
        from platform.connectors.embeddings.cohere_embed import CohereEmbeddingProvider
        return CohereEmbeddingProvider(cfg)

    elif provider == "ollama":
        from platform.connectors.embeddings.ollama_embed import OllamaEmbeddingProvider
        return OllamaEmbeddingProvider(cfg)

    elif provider == "huggingface":
        from platform.connectors.embeddings.huggingface_embed import HuggingFaceEmbeddingProvider
        return HuggingFaceEmbeddingProvider(cfg)

    else:
        raise ValueError(
            f"Unknown embedding provider: '{provider}'. "
            f"Supported: openai, cohere, ollama, huggingface"
        )

# Singleton — loaded once at import time
try:
    _provider = _load_provider()
    logger.info(
        f"Embedding provider ready — "
        f"{_provider.provider_name}  dims={_provider.dimensions}"
    )
except Exception as e:
    logger.error(f"Failed to load embedding provider: {e}")
    _provider = None


# ── Public API ────────────────────────────────────────────────────────────────

def get_embedding(text: str) -> Optional[List[float]]:
    """
    Embed a single text string.

    Returns:
        Float vector, or None on failure.
    """
    if not _provider:
        return None
    try:
        vectors = _provider.embed([text])
        return vectors[0] if vectors else None
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        return None


def get_embeddings(texts: List[str]) -> Optional[List[List[float]]]:
    """
    Embed a list of texts.

    Returns:
        List of float vectors, or None on failure.
    """
    if not _provider:
        return None
    try:
        return _provider.embed(texts)
    except Exception as e:
        logger.error(f"Batch embedding failed: {e}")
        return None


def get_dimensions() -> int:
    """Return vector dimension for current provider."""
    return _provider.dimensions if _provider else 1536


def get_provider_info() -> dict:
    """Return current embedding provider info — used by health endpoints."""
    if not _provider:
        return {"provider": "none", "model": "none", "dimensions": 0, "status": "failed"}
    return {
        "provider":   _provider.provider_name,
        "model":      EMBED_CFG.get("model", "unknown"),
        "dimensions": _provider.dimensions,
        "status":     "ready",
        "config_path": CONFIG_PATH,
    }
