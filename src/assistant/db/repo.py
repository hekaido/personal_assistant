import json
import sqlite3
from datetime import datetime, timezone

import sqlite_vec


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── tracked_dialogs ──────────────────────────────────────────────

def upsert_dialog(
    conn: sqlite3.Connection,
    chat_id: str,
    chat_title: str,
    chat_type: str,
    is_new: int = 0,
    last_message_at: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO tracked_dialogs (chat_id, chat_title, chat_type, tracked, is_new, added_at, last_message_at)
        VALUES (?, ?, ?, 0, ?, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET
            chat_title = excluded.chat_title,
            last_message_at = excluded.last_message_at
        """,
        (chat_id, chat_title, chat_type, is_new, now_iso(), last_message_at),
    )
    conn.commit()


def register_if_unknown(conn: sqlite3.Connection, chat_id: str, chat_title: str, chat_type: str) -> None:
    """Регистрирует диалог, если он ещё не встречался (для новых собеседников после онбординга)."""
    conn.execute(
        """
        INSERT OR IGNORE INTO tracked_dialogs (chat_id, chat_title, chat_type, tracked, is_new, added_at)
        VALUES (?, ?, ?, 0, 1, ?)
        """,
        (chat_id, chat_title, chat_type, now_iso()),
    )
    conn.commit()


def set_tracked(conn: sqlite3.Connection, chat_id: str, tracked: bool) -> None:
    conn.execute(
        "UPDATE tracked_dialogs SET tracked = ?, decided_at = ? WHERE chat_id = ?",
        (int(tracked), now_iso(), chat_id),
    )
    conn.commit()


def get_tracked_chat_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT chat_id FROM tracked_dialogs WHERE tracked = 1").fetchall()
    return {row["chat_id"] for row in rows}


def get_selectable_dialogs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Диалоги, которые можно предложить к выбору в онбординге (каналы туда не попадают вообще).

    Сортировка — по давности последнего сообщения, сначала новые; у кого дата неизвестна
    (last_message_at IS NULL) — в конце (в SQLite NULL меньше любого значения, поэтому
    при ORDER BY ... DESC они естественным образом уходят в самый низ).
    """
    return conn.execute(
        "SELECT * FROM tracked_dialogs WHERE chat_type != 'channel' ORDER BY last_message_at DESC"
    ).fetchall()


def get_dialog(conn: sqlite3.Connection, chat_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tracked_dialogs WHERE chat_id = ?", (chat_id,)).fetchone()


def get_undecided_new_dialogs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM tracked_dialogs WHERE is_new = 1 AND decided_at IS NULL"
    ).fetchall()


# ── messages ──────────────────────────────────────────────────────

def save_message(
    conn: sqlite3.Connection,
    source: str,
    chat_id: str,
    msg_id: str,
    sender: str | None,
    sender_is_me: bool,
    subject: str | None,
    text: str,
    sent_at: str,
) -> bool:
    """Возвращает True, если сообщение было реально вставлено (не дубль)."""
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO messages
            (source, source_chat_id, source_msg_id, sender, sender_is_me, subject, text, sent_at, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (source, chat_id, msg_id, sender, int(sender_is_me), subject, text, sent_at, now_iso()),
    )
    conn.commit()
    return cursor.rowcount > 0


def get_unprocessed_messages(conn: sqlite3.Connection, limit: int, since_iso: str | None = None) -> list[sqlite3.Row]:
    """По умолчанию — самые старые необработанные сначала (чтобы обрабатывать по порядку).
    С since_iso — только сообщения не раньше этой даты, тоже от старых к новым внутри диапазона.
    """
    if since_iso:
        return conn.execute(
            "SELECT * FROM messages WHERE processed_at IS NULL AND sent_at >= ? ORDER BY sent_at ASC LIMIT ?",
            (since_iso, limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM messages WHERE processed_at IS NULL ORDER BY sent_at ASC LIMIT ?",
        (limit,),
    ).fetchall()


def get_chats_with_unprocessed(conn: sqlite3.Connection, since_iso: str | None = None) -> list[str]:
    """Список chat_id, у которых есть хотя бы одно необработанное сообщение."""
    if since_iso:
        rows = conn.execute(
            "SELECT DISTINCT source_chat_id FROM messages WHERE processed_at IS NULL AND sent_at >= ?",
            (since_iso,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT DISTINCT source_chat_id FROM messages WHERE processed_at IS NULL").fetchall()
    return [r["source_chat_id"] for r in rows]


def get_unprocessed_messages_for_chat(
    conn: sqlite3.Connection, chat_id: str, limit: int | None = None, since_iso: str | None = None
) -> list[sqlite3.Row]:
    """Необработанные сообщения ОДНОГО чата, по порядку (от старых к новым)."""
    query = "SELECT * FROM messages WHERE processed_at IS NULL AND source_chat_id = ?"
    params: list = [chat_id]
    if since_iso:
        query += " AND sent_at >= ?"
        params.append(since_iso)
    query += " ORDER BY sent_at ASC"
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    return conn.execute(query, params).fetchall()


def mark_processed(conn: sqlite3.Connection, message_ids: list[int]) -> None:
    if not message_ids:
        return
    now = now_iso()
    conn.executemany(
        "UPDATE messages SET processed_at = ? WHERE id = ?",
        [(now, mid) for mid in message_ids],
    )
    conn.commit()


# ── daily_summaries (иерархическая суммаризация per-чат) ──────────

def get_daily_summary(conn: sqlite3.Connection, chat_id: str, summary_date: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM daily_summaries WHERE chat_id = ? AND summary_date = ?", (chat_id, summary_date)
    ).fetchone()


def get_daily_summary_by_id(conn: sqlite3.Connection, summary_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM daily_summaries WHERE id = ?", (summary_id,)).fetchone()


def get_active_daily_summaries(conn: sqlite3.Connection, since_iso: str) -> list[sqlite3.Row]:
    """Саммари, обновлённые за последние сутки — используется для брифа: чат считается
    активным, если extraction реально что-то по нему обработал в этом окне."""
    return conn.execute(
        "SELECT * FROM daily_summaries WHERE updated_at >= ? ORDER BY updated_at DESC",
        (since_iso,),
    ).fetchall()


def save_daily_summary(conn: sqlite3.Connection, chat_id: str, summary_date: str, content: str) -> int:
    """Upsert; возвращает id саммари (через SELECT — lastrowid ненадёжен при ON CONFLICT UPDATE).

    created_at/updated_at строятся на summary_date (не на времени запуска extraction) —
    иначе при обработке бэклога все саммари помечаются "сегодняшними" независимо от того,
    к какому дню реально относятся, и бриф считает вообще всё "активным за сутки"."""
    now = f"{summary_date}T23:59:59+00:00"
    conn.execute(
        """
        INSERT INTO daily_summaries (chat_id, summary_date, content, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(chat_id, summary_date) DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at
        """,
        (chat_id, summary_date, content, now, now),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM daily_summaries WHERE chat_id = ? AND summary_date = ?", (chat_id, summary_date)
    ).fetchone()["id"]


# ── commitments / facts ──────────────────────────────────────────

def insert_commitment(
    conn: sqlite3.Connection,
    message_id: int,
    type_: str,
    description: str,
    due_at: str | None,
    due_confidence: str,
    participants: list[str],
    occurred_at: str,
    chat_id: str | None = None,
    source_summary_id: int | None = None,
) -> int:
    """occurred_at — когда это реально произошло в переписке (sent_at сообщения-источника),
    а не когда прошёл extraction. created_at/updated_at строятся на нём, иначе "что изменилось
    вчера" при разовой обработке бэклога показывает вообще всё, что не было обработано раньше."""
    cursor = conn.execute(
        """
        INSERT INTO commitments
            (message_id, source_summary_id, chat_id, type, description, due_at, due_confidence,
             status, participants, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)
        """,
        (
            message_id, source_summary_id, chat_id, type_, description, due_at, due_confidence,
            json.dumps(participants, ensure_ascii=False), occurred_at, occurred_at,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def insert_fact(
    conn: sqlite3.Connection,
    message_id: int,
    subject: str,
    fact_text: str,
    tags: list[str],
    occurred_at: str,
    chat_id: str | None = None,
    source_summary_id: int | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO facts (message_id, source_summary_id, chat_id, subject, fact_text, tags, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (message_id, source_summary_id, chat_id, subject, fact_text, json.dumps(tags, ensure_ascii=False), occurred_at),
    )
    conn.commit()
    return cursor.lastrowid


def get_commitment(conn: sqlite3.Connection, commitment_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM commitments WHERE id = ?", (commitment_id,)).fetchone()


def get_fact(conn: sqlite3.Connection, fact_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()


def update_commitment(
    conn: sqlite3.Connection,
    commitment_id: int,
    occurred_at: str,
    description: str | None = None,
    due_at: str | None = None,
    due_confidence: str | None = None,
    status: str | None = None,
) -> None:
    """occurred_at — sent_at сообщения, из-за которого случилось обновление (не now_iso()),
    чтобы "что изменилось вчера" отражало реальные события, а не момент запуска extraction."""
    current = get_commitment(conn, commitment_id)
    if current is None:
        return
    conn.execute(
        "UPDATE commitments SET description = ?, due_at = ?, due_confidence = ?, status = ?, updated_at = ? WHERE id = ?",
        (
            description if description is not None else current["description"],
            due_at if due_at is not None else current["due_at"],
            due_confidence if due_confidence is not None else current["due_confidence"],
            status if status is not None else current["status"],
            occurred_at,
            commitment_id,
        ),
    )
    conn.commit()


def update_fact(conn: sqlite3.Connection, fact_id: int, fact_text: str) -> None:
    conn.execute("UPDATE facts SET fact_text = ? WHERE id = ?", (fact_text, fact_id))
    conn.commit()


# ── embeddings (sqlite-vec) ────────────────────────────────────────

def upsert_embedding(conn: sqlite3.Connection, entity_type: str, entity_id: int, vector: list[float]) -> None:
    """Заменяет эмбеддинг сущности, если уже есть (напр. пересчёт саммари за тот же день)."""
    existing = conn.execute(
        "SELECT rowid FROM embeddings WHERE entity_type = ? AND entity_id = ?", (entity_type, entity_id)
    ).fetchone()
    if existing:
        conn.execute("DELETE FROM embeddings WHERE rowid = ?", (existing["rowid"],))
    conn.execute(
        "INSERT INTO embeddings (entity_type, entity_id, embedding) VALUES (?, ?, ?)",
        (entity_type, entity_id, sqlite_vec.serialize_float32(vector)),
    )
    conn.commit()


def query_similar_embeddings(
    conn: sqlite3.Connection, entity_types: list[str], vector: list[float], top_k: int
) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in entity_types)
    return conn.execute(
        f"""
        SELECT entity_type, entity_id, distance
        FROM embeddings
        WHERE embedding MATCH ? AND k = ? AND entity_type IN ({placeholders})
        ORDER BY distance
        """,
        (sqlite_vec.serialize_float32(vector), top_k, *entity_types),
    ).fetchall()


# ── daily brief (per-chat) ──────────────────────────────────────────

def get_chat_recent_commitments(
    conn: sqlite3.Connection, chat_id: str, since_iso: str, today_end_iso: str
) -> list[sqlite3.Row]:
    """Commitments одного чата, релевантные брифу за последние сутки: либо due_at попадает
    в окно [since_iso, today_end_iso) — это покрывает и "сегодня" (due_at >= now), и
    "просрочено за сутки" (due_at < now) одним условием, — либо запись создана/обновлена
    в этом окне независимо от due_at (например promise без даты, только что зафиксированный)."""
    return conn.execute(
        """
        SELECT * FROM commitments
        WHERE chat_id = ? AND status = 'open' AND (
            (due_at IS NOT NULL AND due_at >= ? AND due_at < ?)
            OR created_at >= ? OR updated_at >= ?
        )
        ORDER BY due_at ASC
        """,
        (chat_id, since_iso, today_end_iso, since_iso, since_iso),
    ).fetchall()


def save_daily_brief(conn: sqlite3.Connection, brief_date: str, content: str) -> None:
    conn.execute(
        """
        INSERT INTO daily_briefs (brief_date, content, generated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(brief_date) DO UPDATE SET content = excluded.content, generated_at = excluded.generated_at
        """,
        (brief_date, content, now_iso()),
    )
    conn.commit()


# ── llm_calls_log ─────────────────────────────────────────────────

def log_llm_call(conn: sqlite3.Connection, provider: str, model: str, purpose: str, input_tokens: int, output_tokens: int) -> None:
    conn.execute(
        """
        INSERT INTO llm_calls_log (provider, model, purpose, input_tokens, output_tokens, cost_usd, created_at)
        VALUES (?, ?, ?, ?, ?, NULL, ?)
        """,
        (provider, model, purpose, input_tokens, output_tokens, now_iso()),
    )
    conn.commit()


# ── sync_state (используем составной ключ "source:chat_id" для per-чатовых курсоров) ──

def _cursor_key(source: str, chat_id: str) -> str:
    return f"{source}:{chat_id}"


def get_sync_cursor(conn: sqlite3.Connection, source: str, chat_id: str) -> str | None:
    row = conn.execute(
        "SELECT last_cursor FROM sync_state WHERE source = ?", (_cursor_key(source, chat_id),)
    ).fetchone()
    return row["last_cursor"] if row else None


def set_sync_cursor(conn: sqlite3.Connection, source: str, chat_id: str, cursor: str) -> None:
    conn.execute(
        """
        INSERT INTO sync_state (source, last_cursor, last_synced_at)
        VALUES (?, ?, ?)
        ON CONFLICT(source) DO UPDATE SET last_cursor = excluded.last_cursor, last_synced_at = excluded.last_synced_at
        """,
        (_cursor_key(source, chat_id), cursor, now_iso()),
    )
    conn.commit()
