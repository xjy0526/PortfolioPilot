"""Application-lifetime embedding providers with explicit production policy."""
from __future__ import annotations

import hashlib
import importlib.metadata
import logging
import re
from typing import Protocol, runtime_checkable

import numpy as np

from config import Settings, settings

logger = logging.getLogger(__name__)


class EmbeddingConfigurationError(RuntimeError):
    """Raised when the configured embedding provider cannot be used safely."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    provider_name: str
    model_name: str
    model_version: str
    dimensions: int
    semantic: bool

    def encode(self, texts: list[str]) -> np.ndarray:
        """Encode texts as a normalized two-dimensional float32 array."""


class HashingEmbeddingProvider:
    """Deterministic lexical fallback; it is not a semantic embedding model."""

    provider_name = "hashing"
    semantic = False

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions
        self.model_name = f"hashing-blake2b-{dimensions}"
        self.model_version = "1"

    def encode(self, texts: list[str]) -> np.ndarray:
        vectors = np.zeros((len(texts), self.dimensions), dtype=np.float32)
        for row, text in enumerate(texts):
            tokens = re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
            for token in tokens:
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                bucket = int.from_bytes(digest[:4], "little") % self.dimensions
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vectors[row, bucket] += sign
            norm = np.linalg.norm(vectors[row])
            if norm > 0:
                vectors[row] /= norm
        return vectors


class SentenceTransformerEmbeddingProvider:
    provider_name = "sentence_transformers"
    semantic = True

    def __init__(
        self,
        model_name: str,
        *,
        expected_dimensions: int,
        local_files_only: bool = False,
    ) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.model = SentenceTransformer(model_name, local_files_only=local_files_only)
        dimensions = self.model.get_embedding_dimension()
        if dimensions is None:
            probe = np.asarray(self.model.encode(["dimension probe"]))
            dimensions = int(probe.shape[1])
        self.dimensions = int(dimensions)
        if self.dimensions != expected_dimensions:
            raise EmbeddingConfigurationError(
                f"Embedding dimension mismatch: expected {expected_dimensions}, "
                f"got {self.dimensions}"
            )
        self.model_version = _sentence_transformer_version(self.model)

    def encode(self, texts: list[str]) -> np.ndarray:
        vectors = np.asarray(
            self.model.encode(texts, normalize_embeddings=True),
            dtype=np.float32,
        )
        if vectors.ndim != 2 or vectors.shape[1] != self.dimensions:
            raise EmbeddingConfigurationError(
                f"Embedding provider returned invalid shape: {tuple(vectors.shape)}"
            )
        return vectors


def build_embedding_provider(
    configuration: Settings = settings,
) -> EmbeddingProvider:
    """Build one configured provider; fallback is explicit and never production-safe."""
    provider = configuration.EMBEDDING_PROVIDER.strip().lower()
    if provider == "hashing":
        if configuration.ENVIRONMENT == "production" or not configuration.hashing_fallback_allowed:
            raise EmbeddingConfigurationError(
                "Hashing embedding is restricted to development and test environments"
            )
        return HashingEmbeddingProvider(configuration.RAG_EMBEDDING_DIMENSION)
    if provider != "sentence_transformers":
        raise EmbeddingConfigurationError(f"Unsupported embedding provider: {provider}")

    try:
        return SentenceTransformerEmbeddingProvider(
            configuration.RAG_EMBEDDING_MODEL,
            expected_dimensions=configuration.RAG_EMBEDDING_DIMENSION,
            local_files_only=configuration.ENVIRONMENT == "production",
        )
    except Exception as exc:
        fallback_allowed = (
            configuration.ENVIRONMENT in {"development", "test"}
            and configuration.hashing_fallback_allowed
        )
        if not fallback_allowed:
            raise EmbeddingConfigurationError(
                f"SentenceTransformer model could not be loaded: {type(exc).__name__}"
            ) from exc
        logger.warning(
            "SentenceTransformer unavailable; using non-semantic development fallback: %s",
            type(exc).__name__,
        )
        return HashingEmbeddingProvider(configuration.RAG_EMBEDDING_DIMENSION)


def _sentence_transformer_version(model: object) -> str:
    commit_hash = None
    try:
        first_module = model[0]  # type: ignore[index]
        config = getattr(getattr(first_module, "auto_model", None), "config", None)
        commit_hash = getattr(config, "_commit_hash", None)
    except (IndexError, KeyError, TypeError):
        commit_hash = None
    if commit_hash:
        return str(commit_hash)
    try:
        package_version = importlib.metadata.version("sentence-transformers")
    except importlib.metadata.PackageNotFoundError:
        package_version = "unknown"
    return f"sentence-transformers-{package_version}"


# Compatibility names for existing imports and tests.
HashingEmbedder = HashingEmbeddingProvider
SentenceTransformerEmbedder = SentenceTransformerEmbeddingProvider
