"""
ollama_embed.py — Ollama local embedding provider (free, on-premise).

Supports: nomic-embed-text (768-dim), mxbai-embed-large (1024-dim)
Requires Ollama running locally or in Docker.

Config keys:
    url    -- Ollama base URL (default: http://ollama:11434)
    model  -- embedding model (default: nomic-embed-text)
"""

import json
import urllib.request
from typing import List

from platform.connectors.embeddings.base import EmbeddingProvider

DIMS = {
    "nomic-embed-text":   768,
    "mxbai-embed-large": 1024,
    "all-minilm":         384,
}


class OllamaEmbeddingProvider(EmbeddingProvider):

    def validate_config(self):
        if not self.config.get("url"):
            self.config["url"] = "http://ollama:11434"

    @property
    def model(self) -> str:
        return self.config.get("model", "nomic-embed-text")

    @property
    def base_url(self) -> str:
        return self.config.get("url", "http://ollama:11434").rstrip("/")

    @property
    def dimensions(self) -> int:
        return DIMS.get(self.model, 768)

    def embed(self, texts: List[str]) -> List[List[float]]:
        """
        Ollama embed API takes one text at a time.
        Batches by calling sequentially.
        """
        embeddings = []
        for text in texts:
            payload = json.dumps({
                "model":  self.model,
                "prompt": text,
            }).encode()

            req = urllib.request.Request(
                f"{self.base_url}/api/embeddings",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())

            if "embedding" not in data:
                raise RuntimeError(
                    f"Ollama embedding error — model '{self.model}' "
                    f"may not be pulled. Run: ollama pull {self.model}"
                )
            embeddings.append(data["embedding"])

        return embeddings
