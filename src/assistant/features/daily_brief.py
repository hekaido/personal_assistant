"""Бриф дня — по чатам, без LLM-вызова.

Текст "о чём говорилось" уже написан на этапе extraction (daily_summaries.content),
инсайты уже структурированы (commitments). Брифу больше не нужно ничего "понимать" —
только собрать по одному активному чату за раз в читаемый блок. LLM-вызов на этом шаге
убран намеренно: два предыдущих подхода (один большой JSON на все чаты разом) либо
переполняли контекст модели, либо она путалась и возвращала пустые заголовки —
детерминированное шаблонирование поверх уже готового текста надёжнее.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

from assistant.db import repo

TYPE_ICON = {"meeting": "🗓", "deadline": "⏰", "task": "📌", "promise": "🤝"}
CHAT_ICON = {"user": "👤", "group": "👥", "bot": "🤖"}


def _format_time(due_at: str | None) -> str:
    if not due_at:
        return ""
    # ISO8601 с временем ("...T18:00:00+00:00") — len >= 16 после даты; чистая дата ("2026-10-05") короче.
    return f" — {due_at[11:16]}" if len(due_at) >= 16 else f" — {due_at}"


def _format_commitment(row: sqlite3.Row) -> str:
    icon = TYPE_ICON.get(row["type"], "•")
    return f"{icon} {row['description']}{_format_time(row['due_at'])}"


def build_daily_brief(conn: sqlite3.Connection) -> str:
    now = datetime.now(timezone.utc)
    since_iso = (now - timedelta(days=1)).isoformat()
    today_end_iso = (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat()

    active_summaries = repo.get_active_daily_summaries(conn, since_iso)
    if not active_summaries:
        return "За последние сутки не было активности ни в одном отслеживаемом чате."

    blocks = []
    for summary_row in active_summaries:
        chat_id = summary_row["chat_id"]
        dialog = repo.get_dialog(conn, chat_id)
        chat_title = dialog["chat_title"] if dialog else chat_id
        icon = CHAT_ICON.get(dialog["chat_type"], "💬") if dialog else "💬"

        commitments = repo.get_chat_recent_commitments(conn, chat_id, since_iso, today_end_iso)

        block = f"### {icon} {chat_title}\n{summary_row['content']}"
        if commitments:
            block += "\n\n" + "\n".join(_format_commitment(c) for c in commitments)
        blocks.append(block)

    return "\n\n---\n\n".join(blocks)


def run_daily_brief(conn: sqlite3.Connection) -> str:
    text = build_daily_brief(conn)
    today_str = datetime.now(timezone.utc).date().isoformat()
    repo.save_daily_brief(conn, today_str, text)
    return text
