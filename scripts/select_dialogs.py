"""Онбординг: выбор, какие диалоги TG отслеживать.

Запускать после telegram_auth.py. Можно запускать повторно в любой момент,
чтобы поменять список — старые решения (tracked=1/0) не сбрасываются,
если ты не укажешь диалог заново.

Запуск:
    .venv/bin/python scripts/select_dialogs.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from telethon import TelegramClient

from assistant.config import load_config, load_secrets
from assistant.db import repo
from assistant.db.connection import connect, init_schema
from assistant.ingestion.telegram_ingestor import classify_chat_type


def parse_selection(raw: str, total: int) -> set[int]:
    raw = raw.strip().lower()
    if raw == "all":
        return set(range(1, total + 1))
    if raw in ("", "none"):
        return set()

    result: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            result.update(range(int(a), int(b) + 1))
        else:
            result.add(int(part))
    return result


async def main() -> None:
    config = load_config()
    secrets = load_secrets()

    conn = connect(config.db_path)
    init_schema(conn)

    client = TelegramClient(
        str(config.telegram_session_path),
        secrets.telegram_api_id,
        secrets.telegram_api_hash,
    )
    await client.start()

    discovered = []
    async for dialog in client.iter_dialogs():
        chat_type = classify_chat_type(dialog.entity)
        if chat_type == "channel":
            continue  # каналы никогда не предлагаются к выбору
        chat_id = str(dialog.id)
        title = dialog.name or chat_id
        last_message_at = dialog.date.isoformat() if dialog.date else None
        repo.upsert_dialog(conn, chat_id, title, chat_type, is_new=0, last_message_at=last_message_at)
        discovered.append((chat_id, title, chat_type))

    await client.disconnect()
    conn.close()

    if not discovered:
        print("Личных чатов/групп не найдено (кроме каналов, которые исключены).")
        return

    print(f"\nНайдено {len(discovered)} диалогов (каналы исключены):\n")
    for i, (_, title, chat_type) in enumerate(discovered, 1):
        print(f"  {i:3d}. [{chat_type:5s}] {title}")

    print(
        "\nВведи номера диалогов для отслеживания через запятую "
        "(например: 1,3,7-9), 'all' — все, Enter/'none' — ни одного:"
    )
    raw = input("> ")
    selected = parse_selection(raw, len(discovered))

    conn = connect(config.db_path)
    for i, (chat_id, _, _) in enumerate(discovered, 1):
        repo.set_tracked(conn, chat_id, tracked=(i in selected))
    conn.close()

    print(f"\nГотово: отслеживается {len(selected)} из {len(discovered)} диалогов.")
    print("Чтобы поменять список позже — запусти этот скрипт снова.")


if __name__ == "__main__":
    asyncio.run(main())
