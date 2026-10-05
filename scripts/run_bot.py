"""Запускает Telegram-бота (long polling).

Свой Telegram user id брать вручную не нужно — определяется автоматически через
уже авторизованную Telethon-сессию (тот же аккаунт, что читает переписку).

Запуск:
    .venv/bin/python scripts/run_bot.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from telethon import TelegramClient

from assistant.bot.telegram_bot import build_application
from assistant.config import load_config, load_secrets
from assistant.db.connection import connect, init_schema
from assistant.llm.factory import LLMFactory


async def _get_my_telegram_id(config, secrets) -> int:
    client = TelegramClient(
        str(config.telegram_session_path), secrets.telegram_api_id, secrets.telegram_api_hash
    )
    await client.start()
    me = await client.get_me()
    await client.disconnect()
    return me.id


def main() -> None:
    config = load_config()
    secrets = load_secrets()

    if not secrets.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN пуст в .env — создай бота через @BotFather и впиши токен")

    conn = connect(config.db_path)
    init_schema(conn)

    factory = LLMFactory(config, secrets)
    embedder = factory.get("embeddings")
    chat_provider = factory.get("chat")

    allowed_user_id = asyncio.run(_get_my_telegram_id(config, secrets))
    print(f"Бот будет отвечать только пользователю с id={allowed_user_id}")

    app = build_application(secrets.telegram_bot_token, conn, embedder, chat_provider, allowed_user_id)
    print("Бот запущен (long polling). Ctrl-C — остановить.")
    app.run_polling()


if __name__ == "__main__":
    main()
