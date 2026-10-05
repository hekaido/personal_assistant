import json
from typing import Any

import httpx

from assistant.llm.base import LLMResponse, Message, ToolCall, ToolDef

ROUTERAI_BASE_URL = "https://routerai.ru/api/v1"


def _messages_to_openai(messages: list[Message], system: str | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if system:
        result.append({"role": "system", "content": system})

    for m in messages:
        if m.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": m.text}
            if m.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.input)},
                    }
                    for tc in m.tool_calls
                ]
            result.append(entry)
        else:
            if m.text:
                result.append({"role": "user", "content": m.text})
            # tool_results у OpenAI-совместимого API — отдельные сообщения с role="tool"
            for tr in m.tool_results:
                result.append({
                    "role": "tool",
                    "tool_call_id": tr.tool_call_id,
                    "content": tr.content,
                })
    return result


def _tools_to_openai(tools: list[ToolDef]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


def _normalize_stop_reason(finish_reason: str | None) -> str:
    if finish_reason == "tool_calls":
        return "tool_use"
    if finish_reason == "length":
        return "max_tokens"
    return "end_turn"


class RouterAIProvider:
    """Провайдер поверх RouterAI (routerai.ru) — OpenAI-совместимый API, один ключ ко многим моделям.

    model должен быть точным slug'ом из каталога RouterAI (см. личный кабинет/документацию
    на routerai.ru) — не проверено автоматически, свериться перед первым реальным запуском.
    """

    def __init__(self, api_key: str, model: str, base_url: str = ROUTERAI_BASE_URL):
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=120,
        )

    async def complete(
        self,
        messages: list[Message],
        system: str | None = None,
        tools: list[ToolDef] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": _messages_to_openai(messages, system),
        }
        if tools:
            payload["tools"] = _tools_to_openai(tools)

        response = await self._client.post("/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()

        choice = data["choices"][0]
        message = choice["message"]

        tool_calls = [
            ToolCall(
                id=tc["id"],
                name=tc["function"]["name"],
                input=json.loads(tc["function"]["arguments"]),
            )
            for tc in message.get("tool_calls") or []
        ]

        usage = data.get("usage", {})
        return LLMResponse(
            text=message.get("content") or "",
            tool_calls=tool_calls,
            stop_reason=_normalize_stop_reason(choice.get("finish_reason")),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )

    async def embed(self, text: str) -> list[float]:
        raise NotImplementedError("RouterAIProvider не считает эмбеддинги — используй local-провайдер")
