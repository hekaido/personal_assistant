"""Провайдер-нейтральный формат сообщений.

Ключевая идея: features/* и extraction/* работают ТОЛЬКО с типами из этого файла
и никогда не видят wire-формат конкретного API (Anthropic content-блоки vs
OpenAI/OpenRouter tool_calls). Каждый провайдер сам переводит эти типы в свой
формат внутри complete() и обратно при разборе ответа — это и даёт настоящую
взаимозаменяемость провайдеров, а не только общий интерфейс на бумаге.
"""
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Role = Literal["user", "assistant"]


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    role: Role
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)   # assistant просит вызвать инструмент(ы)
    tool_results: list[ToolResult] = field(default_factory=list)  # user возвращает результат(ы)


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema — формат одинаков для Anthropic и OpenAI (parameters)


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"   # нормализовано: "end_turn" | "tool_use" | "max_tokens"
    input_tokens: int = 0
    output_tokens: int = 0

    def to_message(self) -> Message:
        """Ответ модели, приведённый обратно к Message — чтобы добавить в историю диалога."""
        return Message(role="assistant", text=self.text or None, tool_calls=self.tool_calls)


class LLMProvider(Protocol):
    async def complete(
        self,
        messages: list[Message],
        system: str | None = None,
        tools: list[ToolDef] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse: ...

    async def embed(self, text: str) -> list[float]: ...
