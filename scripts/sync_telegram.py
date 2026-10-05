"""Разовая синхронизация сообщений из отслеживаемых диалогов Telegram в БД.

Пока используется вручную для проверки/наполнения; в конечной версии эту же
логику дёргает APScheduler-джоба на регулярной основе (см. scheduler/jobs.py).

Запуск:
    .venv/bin/python scripts/sync_telegram.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from telethon import TelegramClient

from assistant.config import load_config, load_secrets
from assistant.db.connection import connect, init_schema
from assistant.ingestion.telegram_ingestor import TelegramIngestor


async def main() -> None:
    config = load_config()
    secrets = load_secrets()

    conn = connect(config.db_path)
    init_schema(conn)

    client = TelegramClient(
        str(config.telegram_session_path), secrets.telegram_api_id, secrets.telegram_api_hash
    )
    await client.start()

    ingestor = TelegramIngestor(client, conn, initial_history_months=config.telegram.initial_history_months)
    print("Синхронизирую отслеживаемые диалоги...")
    new_count = await ingestor.sync()
    print(f"Готово: новых сообщений сохранено — {new_count}")

    await client.disconnect()
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
