"""
openai_embed.py — OpenAI text-embedding provider.

Supports: text-embedding-3-small (1536-dim), text-embedding-3-large (3072-dim)
Uses urllib directly — no openai package needed, no dependency conflicts.

Config keys:
    api_key  -- OpenAI API key (or ${OPENAI_API_KEY})
    model    -- embedding model name (default: text-embedding-3-small)
"""

import json
import os
import urllib.request
from typing import List

from platform.connectors.embeddings.base import EmbeddingProvider

DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002":  1536,
}


class OpenAIEmbeddingProvider(EmbeddingProvider):

    def validate_config(self):
        if not self.config.get("api_key"):
            self.config["api_key"] = os.getenv("OPENAI_API_KEY", "")
        if not self.config.get("api_key"):
            raise ValueError(
                "OpenAI embedding requires api_key in config or OPENAI_API_KEY env var"
            )

    @property
    def model(self) -> str:
        return self.config.get("model", "text-embedding-3-small")

    @property
    def dimensions(self) -> int:
        return DIMS.get(self.model, 1536)

    def embed(self, texts: List[str]) -> List[List[float]]:
        payload = json.dumps({
            "model": self.model,
            "input": texts
        }).encode()

        req = urllib.request.Request(
            "https://api.openai.com/v1/embeddings",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.config['api_key']}",
                "Content-Type":  "application/json",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())

        if "error" in data:
            raise RuntimeError(f"OpenAI embedding error: {data['error']['message']}")

        return [item["embedding"] for item in data["data"]]
