"""RAG-чат по базе знаний: вопрос → векторный поиск по commitments/facts/summary → ответ LLM.

По схеме: "При запросе — 1) обращение в RAG (топ выдача) 2) формирование ответа".
Модель отвечает ТОЛЬКО на основе найденного контекста и обязана честно сказать,
если ответа в контексте нет — не выдумывать.
"""
import sqlite3

from assistant.db import repo
from assistant.llm.base import LLMProvider, Message

TOP_K = 15

SYSTEM_PROMPT = """Ты — личный ассистент пользователя, отвечаешь на вопросы по его переписке.

Отвечай ТОЛЬКО на основе контекста ниже — фрагментов из его чатов (саммари дня,
договорённости, факты). Если ответа в контексте нет — прямо скажи, что не нашёл,
не выдумывай и не додумывай.

Когда это уместно, указывай источник (какой чат, какая дата) — коротко, в скобках.
Пиши по-русски, по делу, без лишних вступлений."""


def _entity_to_context_line(conn: sqlite3.Connection, entity_type: str, entity_id: int) -> str | None:
    if entity_type == "commitment":
        row = repo.get_commitment(conn, entity_id)
        if row is None:
            return None
        dialog = repo.get_dialog(conn, row["chat_id"]) if row["chat_id"] else None
        chat_title = dialog["chat_title"] if dialog else (row["chat_id"] or "?")
        due = f", due={row['due_at']}" if row["due_at"] else ""
        return f"[договорённость, чат «{chat_title}», статус={row['status']}{due}] {row['description']}"

    if entity_type == "fact":
        row = repo.get_fact(conn, entity_id)
        if row is None:
            return None
        dialog = repo.get_dialog(conn, row["chat_id"]) if row["chat_id"] else None
        chat_title = dialog["chat_title"] if dialog else (row["chat_id"] or "?")
        return f"[факт, чат «{chat_title}»] {row['fact_text']}"

    if entity_type == "summary":
        row = repo.get_daily_summary_by_id(conn, entity_id)
        if row is None:
            return None
        dialog = repo.get_dialog(conn, row["chat_id"])
        chat_title = dialog["chat_title"] if dialog else row["chat_id"]
        return f"[саммари дня, чат «{chat_title}», {row['summary_date']}] {row['content']}"

    return None


async def answer_question(
    conn: sqlite3.Connection, embedder: LLMProvider, chat_provider: LLMProvider, question: str, top_k: int = TOP_K
) -> str:
    vector = await embedder.embed(question)
    results = repo.query_similar_embeddings(conn, ["commitment", "fact", "summary"], vector, top_k)

    context_lines = []
    for r in results:
        line = _entity_to_context_line(conn, r["entity_type"], r["entity_id"])
        if line:
            context_lines.append(line)

    if not context_lines:
        return "В базе знаний пока ничего не нашлось по этому вопросу."

    context = "\n\n".join(context_lines)
    prompt = f"Контекст из переписки:\n\n{context}\n\n---\n\nВопрос: {question}"

    response = await chat_provider.complete(
        messages=[Message(role="user", text=prompt)],
        system=SYSTEM_PROMPT,
        max_tokens=2048,
    )
    return response.text.strip()
