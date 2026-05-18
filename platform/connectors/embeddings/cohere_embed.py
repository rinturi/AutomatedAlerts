"""
cohere_embed.py — Cohere embedding provider.

Supports: embed-english-v3.0 (1024-dim), embed-multilingual-v3.0 (1024-dim)
Uses urllib directly — no cohere package needed.

Config keys:
    api_key   -- Cohere API key (or ${COHERE_API_KEY})
    model     -- embedding model (default: embed-english-v3.0)
    input_type-- search_document | search_query (default: search_document)
"""

import json
import os
import urllib.request
from typing import List

from platform.connectors.embeddings.base import EmbeddingProvider

DIMS = {
    "embed-english-v3.0":        1024,
    "embed-multilingual-v3.0":   1024,
    "embed-english-light-v3.0":   384,
}


class CohereEmbeddingProvider(EmbeddingProvider):

    def validate_config(self):
        if not self.config.get("api_key"):
            self.config["api_key"] = os.getenv("COHERE_API_KEY", "")
        if not self.config.get("api_key"):
            raise ValueError(
                "Cohere embedding requires api_key in config or COHERE_API_KEY env var"
            )

    @property
    def model(self) -> str:
        return self.config.get("model", "embed-english-v3.0")

    @property
    def dimensions(self) -> int:
        return DIMS.get(self.model, 1024)

    def embed(self, texts: List[str]) -> List[List[float]]:
        payload = json.dumps({
            "model":      self.model,
            "texts":      texts,
            "input_type": self.config.get("input_type", "search_document"),
        }).encode()

        req = urllib.request.Request(
            "https://api.cohere.ai/v1/embed",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.config['api_key']}",
                "Content-Type":  "application/json",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())

        if "message" in data:
            raise RuntimeError(f"Cohere embedding error: {data['message']}")

        return data["embeddings"]
