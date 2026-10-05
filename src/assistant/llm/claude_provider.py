from typing import Any

from anthropic import AsyncAnthropic

from assistant.llm.base import LLMResponse, Message, ToolCall, ToolDef


def _messages_to_anthropic(messages: list[Message]) -> list[dict[str, Any]]:
    result = []
    for m in messages:
        content: list[dict[str, Any]] = []
        if m.text:
            content.append({"type": "text", "text": m.text})
        for tc in m.tool_calls:
            content.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input})
        for tr in m.tool_results:
            content.append({
                "type": "tool_result",
                "tool_use_id": tr.tool_call_id,
                "content": tr.content,
                "is_error": tr.is_error,
            })
        result.append({"role": m.role, "content": content})
    return result


def _tools_to_anthropic(tools: list[ToolDef]) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]


def _normalize_stop_reason(stop_reason: str | None) -> str:
    if stop_reason == "tool_use":
        return "tool_use"
    if stop_reason == "max_tokens":
        return "max_tokens"
    return "end_turn"


class ClaudeProvider:
    def __init__(self, api_key: str, model: str):
        self.model = model
        self._client = AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        messages: list[Message],
        system: str | None = None,
        tools: list[ToolDef] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": _messages_to_anthropic(messages),
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = _tools_to_anthropic(tools)

        response = await self._client.messages.create(**kwargs)

        text_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))

        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=_normalize_stop_reason(response.stop_reason),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    async def embed(self, text: str) -> list[float]:
        raise NotImplementedError("ClaudeProvider не считает эмбеддинги — используй local-провайдер")
