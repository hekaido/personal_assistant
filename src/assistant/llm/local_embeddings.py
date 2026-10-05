from typing import Any

from sentence_transformers import SentenceTransformer

from assistant.llm.base import LLMResponse


class LocalEmbeddingsProvider:
    """Считает эмбеддинги локально (sentence-transformers), не умеет chat/extraction."""

    def __init__(self, model: str):
        self._model = SentenceTransformer(model)

    async def complete(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        raise NotImplementedError("LocalEmbeddingsProvider не умеет chat/completion")

    async def embed(self, text: str) -> list[float]:
        vector = self._model.encode(text, normalize_embeddings=True)
        return vector.tolist()
