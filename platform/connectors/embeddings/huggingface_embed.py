"""
huggingface_embed.py — HuggingFace local embedding provider (free, on-premise).

Supports: sentence-transformers models (e.g. all-MiniLM-L6-v2, 384-dim)
Requires sentence-transformers installed in the container.

Config keys:
    model   -- HuggingFace model name (default: sentence-transformers/all-MiniLM-L6-v2)
    device  -- cpu | cuda (default: cpu)
"""

from typing import List
from platform.connectors.embeddings.base import EmbeddingProvider

DIMS = {
    "sentence-transformers/all-MiniLM-L6-v2":  384,
    "sentence-transformers/all-mpnet-base-v2": 768,
    "BAAI/bge-small-en-v1.5":                  384,
    "BAAI/bge-base-en-v1.5":                   768,
}


class HuggingFaceEmbeddingProvider(EmbeddingProvider):

    def validate_config(self):
        try:
            from sentence_transformers import SentenceTransformer  # noqa
        except ImportError:
            raise ImportError(
                "HuggingFace embedding requires sentence-transformers. "
                "Add to requirements.txt: sentence-transformers>=2.7.0"
            )

    @property
    def model(self) -> str:
        return self.config.get(
            "model", "sentence-transformers/all-MiniLM-L6-v2"
        )

    @property
    def dimensions(self) -> int:
        return DIMS.get(self.model, 384)

    def _get_encoder(self):
        from sentence_transformers import SentenceTransformer
        device = self.config.get("device", "cpu")
        return SentenceTransformer(self.model, device=device)

    def embed(self, texts: List[str]) -> List[List[float]]:
        encoder    = self._get_encoder()
        embeddings = encoder.encode(texts, convert_to_numpy=True)
        return embeddings.tolist()
