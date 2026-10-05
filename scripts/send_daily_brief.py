"""Генерирует бриф дня и отправляет его в Telegram "Избранное" (Saved Messages).

Отдельного бота под уведомления мы ещё не строили — пока используем уже
авторизованную пользовательскую сессию. Позже (bot/telegram_bot.py) можно
будет слать через отдельного бота, ничего в daily_brief.py менять не придётся.

Запуск:
    .venv/bin/python scripts/send_daily_brief.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from telethon import TelegramClient

from assistant.bot.formatters import split_for_telegram
from assistant.config import load_config, load_secrets
from assistant.db.connection import connect, init_schema
from assistant.features.daily_brief import run_daily_brief


async def main() -> None:
    config = load_config()
    secrets = load_secrets()

    conn = connect(config.db_path)
    init_schema(conn)

    text = run_daily_brief(conn)
    print("=== БРИФ ДНЯ ===\n")
    print(text)
    print()

    client = TelegramClient(
        str(config.telegram_session_path), secrets.telegram_api_id, secrets.telegram_api_hash
    )
    await client.start()
    chunks = split_for_telegram(text)
    for chunk in chunks:
        await client.send_message("me", chunk, parse_mode="markdown")
    await client.disconnect()

    print(f"Отправлено в Избранное ({len(chunks)} сообщени{'е' if len(chunks) == 1 else 'й'}).")
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
