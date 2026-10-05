from functools import lru_cache

from assistant.config import AppConfig, Secrets
from assistant.llm.base import LLMProvider
from assistant.llm.claude_provider import ClaudeProvider
from assistant.llm.local_embeddings import LocalEmbeddingsProvider
from assistant.llm.router_ai_provider import RouterAIProvider

TASKS = ("summarize", "extraction", "chat", "brief", "embeddings")


def _build_provider(provider: str, model: str, secrets: Secrets) -> LLMProvider:
    if provider == "claude":
        if not secrets.anthropic_api_key:
            raise ValueError("Провайдер 'claude' выбран в config.yaml, но ANTHROPIC_API_KEY пуст в .env")
        return ClaudeProvider(api_key=secrets.anthropic_api_key, model=model)
    if provider == "router_ai":
        if not secrets.routerai_api_key:
            raise ValueError("Провайдер 'router_ai' выбран в config.yaml, но ROUTERAI_API_KEY пуст в .env")
        return RouterAIProvider(api_key=secrets.routerai_api_key, model=model)
    if provider == "local":
        return LocalEmbeddingsProvider(model=model)
    raise ValueError(f"Неизвестный LLM-провайдер: {provider}")


class LLMFactory:
    """Инстанцирует провайдера под каждую задачу согласно config.yaml (llm.<task>)."""

    def __init__(self, config: AppConfig, secrets: Secrets):
        self._config = config
        self._secrets = secrets
        self._cache: dict[str, LLMProvider] = {}

    def get(self, task: str) -> LLMProvider:
        if task not in TASKS:
            raise ValueError(f"Неизвестная задача: {task}. Допустимые: {TASKS}")
        if task not in self._cache:
            task_config = getattr(self._config.llm, task)
            self._cache[task] = _build_provider(task_config.provider, task_config.model, self._secrets)
        return self._cache[task]


@lru_cache
def get_factory() -> LLMFactory:
    from assistant.config import load_config, load_secrets

    return LLMFactory(load_config(), load_secrets())
