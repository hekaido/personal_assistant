"""Telegram-бот: /brief по команде, любой другой текст — вопрос к RAG-чату.

Приватный — отвечает только пользователю с allowed_user_id (владелец), всем
остальным — отказ. Это не публичный бот, а личный интерфейс к своей же БД.
"""
import logging
import sqlite3

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from assistant.bot.formatters import split_for_telegram
from assistant.features.chat_kb import answer_question
from assistant.features.daily_brief import run_daily_brief
from assistant.llm.base import LLMProvider

logger = logging.getLogger(__name__)


def build_application(
    token: str,
    conn: sqlite3.Connection,
    embedder: LLMProvider,
    chat_provider: LLMProvider,
    allowed_user_id: int,
) -> Application:
    app = Application.builder().token(token).build()

    async def _authorized(update: Update) -> bool:
        if update.effective_user is None or update.effective_user.id != allowed_user_id:
            if update.effective_message:
                await update.effective_message.reply_text("Этот бот приватный.")
            return False
        return True

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _authorized(update):
            return
        await update.message.reply_text(
            "Привет! Я твой личный ассистент.\n\n"
            "/brief — бриф дня по всем активным чатам\n"
            "Просто напиши вопрос — отвечу на основе твоей переписки."
        )

    async def brief(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _authorized(update):
            return
        text = run_daily_brief(conn)
        for chunk in split_for_telegram(text):
            await update.message.reply_text(chunk, parse_mode="Markdown")

    async def handle_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _authorized(update):
            return
        question = update.message.text
        await update.effective_chat.send_action("typing")
        try:
            answer = await answer_question(conn, embedder, chat_provider, question)
        except Exception as e:
            logger.exception("Ошибка при ответе на вопрос")
            answer = f"Что-то пошло не так: {e}"
        for chunk in split_for_telegram(answer):
            await update.message.reply_text(chunk)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("brief", brief))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_question))

    return app
