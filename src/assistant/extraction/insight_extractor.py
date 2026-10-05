"""Извлечение структурированных инсайтов (дедлайны/договорённости/факты) из дневного саммари чата.

В отличие от старого per-message extractor'а, здесь на вход — уже готовое связное
саммари за день по одному чату, а на выходе — список инсайтов сразу (не один item).
"""
from pydantic import ValidationError

from assistant.extraction.schemas import ExtractedItem
from assistant.llm.base import LLMProvider, Message, ToolDef

INSIGHTS_TOOL = ToolDef(
    name="save_insights",
    description=(
        "Сохрани список инсайтов, извлечённых из дневного саммари переписки: дедлайны, "
        "договорённости, встречи, задачи и значимые факты. Если в саммари вообще ничего "
        "такого нет — передай пустой список results."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["deadline", "promise", "meeting", "task", "fact"],
                        },
                        "description": {"type": "string", "description": "Краткая суть на русском"},
                        "due_at": {
                            "type": ["string", "null"],
                            "description": "ISO8601 дата/время, если применимо, иначе null",
                        },
                        "due_confidence": {
                            "type": "string",
                            "enum": ["exact", "approximate", "unclear"],
                        },
                        "participants": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["type", "description", "due_confidence"],
                },
            }
        },
        "required": ["results"],
    },
)

SYSTEM_PROMPT = """Ты извлекаешь структурированные инсайты из дневного саммари переписки в чате "{chat_title}".

Сегодняшняя дата: {today}. Относительные даты в саммари ("завтра", "в четверг") переводи \
в конкретную ISO8601-дату, отталкиваясь от сегодняшней даты.

due_confidence:
- "exact" — названа точная дата/день недели
- "approximate" — есть примерный ориентир без точной даты
- "unclear" — срок не упомянут вовсе

Каждый инсайт — это ОДНА отдельная договорённость/дедлайн/встреча/задача/факт, не пересказ \
всего саммари целиком. Не выдумывай ничего, чего нет в саммари."""


async def extract_insights(
    provider: LLMProvider, chat_title: str, daily_summary: str, today_iso: str
) -> list[ExtractedItem]:
    response = await provider.complete(
        messages=[Message(role="user", text=daily_summary)],
        system=SYSTEM_PROMPT.format(chat_title=chat_title, today=today_iso),
        tools=[INSIGHTS_TOOL],
        max_tokens=2048,
    )

    items: list[ExtractedItem] = []
    for tool_call in response.tool_calls:
        if tool_call.name != "save_insights":
            continue
        results = tool_call.input.get("results", [])
        if isinstance(results, str):
            import json

            try:
                results = json.loads(results)
            except (json.JSONDecodeError, TypeError):
                continue
        for raw in results:
            if not isinstance(raw, dict):
                continue
            try:
                items.append(ExtractedItem.model_validate(raw))
            except ValidationError:
                continue

    return items
