"""Иерархическая суммаризация: батч сообщений → саммари батча → дневное саммари чата.

Заменяет старый двухступенчатый filter+extract на уровне отдельных сообщений —
вместо решения "важно/не важно" по каждому сообщению модель теперь пишет связный
пересказ куска переписки, что даёт больше контекста для последующего извлечения
инсайтов и меньше теряет из-за вырванных из контекста реплик.
"""
from typing import Any

from assistant.llm.base import LLMProvider, Message

BATCH_SYSTEM_PROMPT = """Кратко перескажи, о чём этот кусок переписки в чате "{chat_title}".
Упомяни договорённости, дедлайны, встречи, задачи и значимые факты, если они есть.
Игнорируй бытовой small talk, если в нём нет ничего содержательного.
Пиши связным текстом на русском, 2-5 предложений. Если в куске вообще нет ничего
содержательного — так и напиши одним словом: "Ничего важного"."""

COMBINE_SYSTEM_PROMPT = """У тебя есть саммари переписки в чате "{chat_title}" за сегодня
(может быть пустым, если это первое саммари за день) и новые куски саммари, которые
нужно в него влить. Перепиши в одно связное дневное саммари, не теряя ничего
содержательного из обеих частей и не повторяя одно и то же дважды. Пиши на русском,
кратко и по делу."""


async def summarize_batch(provider: LLMProvider, chat_title: str, messages: list[dict[str, Any]]) -> str:
    """messages: [{sender, text, sent_at}, ...] — один батч сообщений одного чата, по порядку.

    sent_at — полный ISO8601; в промпт идёт только время (HH:MM), дата и так есть в контексте
    (date_key чата/дня) — так модель видит реальные паузы между репликами и точное время
    формулировок вида "договорились на 19:00", а не только относительный порядок строк."""
    body = "\n".join(f"[{m['sent_at'][11:16]}] {m['sender']}: {m['text']}" for m in messages)
    response = await provider.complete(
        messages=[Message(role="user", text=body)],
        system=BATCH_SYSTEM_PROMPT.format(chat_title=chat_title),
        max_tokens=1024,
    )
    return response.text.strip()


async def combine_daily_summary(
    provider: LLMProvider, chat_title: str, existing_summary: str | None, new_batch_summaries: list[str]
) -> str:
    parts = []
    if existing_summary:
        parts.append(f"Текущее саммари за сегодня:\n{existing_summary}")
    parts.append("Новые куски саммари для слияния:\n" + "\n---\n".join(new_batch_summaries))

    response = await provider.complete(
        messages=[Message(role="user", text="\n\n".join(parts))],
        system=COMBINE_SYSTEM_PROMPT.format(chat_title=chat_title),
        max_tokens=2048,
    )
    return response.text.strip()
