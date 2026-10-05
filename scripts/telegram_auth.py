"""Первичная авторизация в Telegram. Запускать интерактивно самому — понадобится
твой номер телефона и код подтверждения, который придёт в Telegram/SMS.

Запуск:
    .venv/bin/python scripts/telegram_auth.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from telethon import TelegramClient

from assistant.config import load_config, load_secrets


async def main() -> None:
    config = load_config()
    secrets = load_secrets()

    client = TelegramClient(
        str(config.telegram_session_path),
        secrets.telegram_api_id,
        secrets.telegram_api_hash,
    )
    await client.start()  # спросит номер телефона и код прямо в терминале

    me = await client.get_me()
    print(f"\nАвторизован как: {me.first_name} (@{me.username or me.id})")
    print(f"Сессия сохранена: {config.telegram_session_path}")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
