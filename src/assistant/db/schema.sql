-- ── Сырые данные ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY,
    source          TEXT NOT NULL CHECK (source IN ('telegram','gmail')),
    source_chat_id  TEXT NOT NULL,
    source_msg_id   TEXT NOT NULL,
    sender          TEXT,
    sender_is_me    INTEGER NOT NULL DEFAULT 0,
    subject         TEXT,
    text            TEXT NOT NULL,
    sent_at         TEXT NOT NULL,
    ingested_at     TEXT NOT NULL,
    processed_at    TEXT,
    UNIQUE(source, source_chat_id, source_msg_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_processed ON messages(processed_at);
CREATE INDEX IF NOT EXISTS idx_messages_sent_at ON messages(sent_at);

-- ── Отслеживаемые диалоги TG ───────────────────────────────────

CREATE TABLE IF NOT EXISTS tracked_dialogs (
    chat_id         TEXT PRIMARY KEY,
    chat_title      TEXT NOT NULL,
    chat_type       TEXT NOT NULL CHECK (chat_type IN ('user','group','channel','bot')),
    tracked         INTEGER NOT NULL DEFAULT 0,
    is_new          INTEGER NOT NULL DEFAULT 0,
    added_at        TEXT NOT NULL,
    decided_at      TEXT,
    last_message_at TEXT,
    category        TEXT   -- произвольный тег для группировки в брифе, напр. 'work'/'personal'
);
CREATE INDEX IF NOT EXISTS idx_tracked_dialogs_tracked ON tracked_dialogs(tracked);

-- ── Саммари переписки (иерархическая суммаризация per-чат) ─────

CREATE TABLE IF NOT EXISTS daily_summaries (
    id            INTEGER PRIMARY KEY,
    chat_id       TEXT NOT NULL,
    summary_date  TEXT NOT NULL,   -- YYYY-MM-DD
    content       TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE(chat_id, summary_date)
);

-- ── Извлечённые сущности ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS commitments (
    id                  INTEGER PRIMARY KEY,
    message_id          INTEGER NOT NULL REFERENCES messages(id),
    source_summary_id   INTEGER REFERENCES daily_summaries(id),
    chat_id             TEXT,
    type                TEXT NOT NULL CHECK (type IN ('deadline','promise','meeting','task')),
    description         TEXT NOT NULL,
    due_at              TEXT,
    due_confidence      TEXT NOT NULL CHECK (due_confidence IN ('exact','approximate','unclear')),
    status              TEXT NOT NULL DEFAULT 'open'
                            CHECK (status IN ('open','done','cancelled','overdue')),
    participants        TEXT,
    confirmed_by_user   INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_commitments_due ON commitments(due_at);
CREATE INDEX IF NOT EXISTS idx_commitments_status ON commitments(status);

CREATE TABLE IF NOT EXISTS facts (
    id                  INTEGER PRIMARY KEY,
    message_id          INTEGER NOT NULL REFERENCES messages(id),
    source_summary_id   INTEGER REFERENCES daily_summaries(id),
    chat_id             TEXT,
    subject             TEXT NOT NULL,
    fact_text           TEXT NOT NULL,
    tags                TEXT,
    created_at          TEXT NOT NULL
);

-- ── Служебные таблицы ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sync_state (
    source          TEXT PRIMARY KEY,
    last_cursor     TEXT,
    last_synced_at  TEXT
);

CREATE TABLE IF NOT EXISTS reminders_sent (
    id              INTEGER PRIMARY KEY,
    commitment_id   INTEGER NOT NULL REFERENCES commitments(id),
    sent_at         TEXT NOT NULL,
    channel         TEXT NOT NULL DEFAULT 'telegram'
);

CREATE TABLE IF NOT EXISTS llm_calls_log (
    id            INTEGER PRIMARY KEY,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    purpose       TEXT NOT NULL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd      REAL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_briefs (
    id            INTEGER PRIMARY KEY,
    brief_date    TEXT NOT NULL UNIQUE,
    content       TEXT NOT NULL,
    generated_at  TEXT NOT NULL
);
