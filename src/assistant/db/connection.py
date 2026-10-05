import sqlite3
from pathlib import Path

import sqlite_vec

EMBEDDING_DIM = 1024  # bge-m3


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl_type: str) -> None:
    existing_cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing_cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")


def _migrate(conn: sqlite3.Connection) -> None:
    """Точечные ALTER TABLE для полей, добавленных после первого релиза схемы.

    schema.sql использует CREATE TABLE IF NOT EXISTS, поэтому на уже существующей
    БД новые таблицы появятся сами, а вот новые КОЛОНКИ существующих таблиц — нет,
    их нужно добавлять отдельно, один раз.
    """
    _add_column_if_missing(conn, "tracked_dialogs", "last_message_at", "TEXT")
    _add_column_if_missing(conn, "tracked_dialogs", "category", "TEXT")
    _add_column_if_missing(conn, "commitments", "source_summary_id", "INTEGER")
    _add_column_if_missing(conn, "commitments", "chat_id", "TEXT")
    _add_column_if_missing(conn, "facts", "source_summary_id", "INTEGER")
    _add_column_if_missing(conn, "facts", "chat_id", "TEXT")


def init_schema(conn: sqlite3.Connection) -> None:
    schema_path = Path(__file__).parent / "schema.sql"
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    conn.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS embeddings USING vec0(
            entity_type   TEXT,
            entity_id     INTEGER,
            embedding     FLOAT[{EMBEDDING_DIM}]
        )
    """)
    _migrate(conn)
    conn.commit()
