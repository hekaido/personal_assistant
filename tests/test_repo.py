import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assistant.db import repo


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema = (Path(__file__).resolve().parents[1] / "src/assistant/db/schema.sql").read_text()
    conn.executescript(schema)
    return conn


def test_upsert_and_tracked_ids():
    conn = make_conn()
    repo.upsert_dialog(conn, "111", "Alice", "user")
    repo.upsert_dialog(conn, "222", "Team chat", "group")
    assert repo.get_tracked_chat_ids(conn) == set()

    repo.set_tracked(conn, "111", True)
    assert repo.get_tracked_chat_ids(conn) == {"111"}

    # обновление title не должно сбрасывать tracked
    repo.upsert_dialog(conn, "111", "Alice Renamed", "user")
    assert repo.get_tracked_chat_ids(conn) == {"111"}
    row = conn.execute("SELECT chat_title FROM tracked_dialogs WHERE chat_id='111'").fetchone()
    assert row["chat_title"] == "Alice Renamed"


def test_register_if_unknown_does_not_override_existing():
    conn = make_conn()
    repo.upsert_dialog(conn, "111", "Alice", "user")
    repo.set_tracked(conn, "111", True)

    repo.register_if_unknown(conn, "111", "Alice", "user")  # уже существует — не должно сломать tracked
    assert repo.get_tracked_chat_ids(conn) == {"111"}

    repo.register_if_unknown(conn, "999", "Stranger", "user")  # новый — is_new=1, tracked=0
    row = conn.execute("SELECT tracked, is_new FROM tracked_dialogs WHERE chat_id='999'").fetchone()
    assert row["tracked"] == 0
    assert row["is_new"] == 1


def test_save_message_dedup():
    conn = make_conn()
    inserted_1 = repo.save_message(
        conn, "telegram", "111", "5001", "Alice", False, None, "привет", "2026-01-01T10:00:00"
    )
    inserted_2 = repo.save_message(
        conn, "telegram", "111", "5001", "Alice", False, None, "привет", "2026-01-01T10:00:00"
    )
    assert inserted_1 is True
    assert inserted_2 is False  # дубль по UNIQUE(source, source_chat_id, source_msg_id)

    count = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
    assert count == 1


def test_sync_cursor_per_chat():
    conn = make_conn()
    assert repo.get_sync_cursor(conn, "telegram", "111") is None

    repo.set_sync_cursor(conn, "telegram", "111", "5001")
    repo.set_sync_cursor(conn, "telegram", "222", "9999")

    assert repo.get_sync_cursor(conn, "telegram", "111") == "5001"
    assert repo.get_sync_cursor(conn, "telegram", "222") == "9999"

    repo.set_sync_cursor(conn, "telegram", "111", "5050")
    assert repo.get_sync_cursor(conn, "telegram", "111") == "5050"


if __name__ == "__main__":
    test_upsert_and_tracked_ids()
    test_register_if_unknown_does_not_override_existing()
    test_save_message_dedup()
    test_sync_cursor_per_chat()
    print("Все тесты repo.py прошли")
