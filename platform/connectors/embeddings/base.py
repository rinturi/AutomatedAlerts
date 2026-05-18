"""
base.py — Abstract base class for all embedding providers.

Every embedding provider must implement embed().
The embed_client.py reads config.yml and instantiates
the correct provider at startup.
"""

from abc import ABC, abstractmethod
from typing import List


class EmbeddingProvider(ABC):

    def __init__(self, config: dict):
        self.config = config
        self._resolve_env_vars()
        self.validate_config()

    def _resolve_env_vars(self):
        """Resolve ${ENV_VAR} placeholders in config values."""
        import os
        for k, v in self.config.items():
            if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                self.config[k] = os.getenv(v[2:-1], "")

    def validate_config(self):
        """Override to check required config keys."""
        pass

    @abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        """
        Embed a list of texts and return a list of float vectors.

        Args:
            texts: List of strings to embed

        Returns:
            List of float vectors, one per input text.
            All vectors have the same dimension (provider-dependent).
        """
        ...

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Return the vector dimension for this provider/model."""
        ...

    @property
    def provider_name(self) -> str:
        return self.__class__.__name__.lower().replace("provider", "")
