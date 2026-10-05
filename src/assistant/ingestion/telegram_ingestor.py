import sqlite3
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat, User

from assistant.db import repo


def classify_chat_type(entity) -> str:
    if isinstance(entity, User):
        return "bot" if entity.bot else "user"
    if isinstance(entity, Chat):
        return "group"
    if isinstance(entity, Channel):
        # Обычный канал (вещание) — broadcast=True, exclude всегда.
        # Супергруппа (megagroup) технически тоже Channel в Telethon, но это обычный чат.
        return "channel" if entity.broadcast else "group"
    return "user"


class TelegramIngestor:
    """Забирает новые сообщения только по диалогам с tracked=1 в tracked_dialogs.

    Диалоги, которых ещё нет в tracked_dialogs (новые собеседники), регистрирует
    с is_new=1, tracked=0 — ingestion их пропускает, пока пользователь явно не
    включит трекинг (через select_dialogs.py или команду /dialogs в боте).
    Каналы (chat_type='channel') не регистрируются вообще.
    """

    def __init__(self, client: TelegramClient, conn: sqlite3.Connection, initial_history_months: int = 6):
        self._client = client
        self._conn = conn
        self._initial_history_months = initial_history_months

    async def sync(self) -> int:
        tracked_ids = repo.get_tracked_chat_ids(self._conn)
        new_messages_count = 0

        async for dialog in self._client.iter_dialogs():
            chat_id = str(dialog.id)
            chat_type = classify_chat_type(dialog.entity)

            if chat_type == "channel":
                continue

            if chat_id not in tracked_ids:
                repo.register_if_unknown(self._conn, chat_id, dialog.name or chat_id, chat_type)
                continue

            new_messages_count += await self._sync_dialog(dialog, chat_id)

        return new_messages_count

    async def _sync_dialog(self, dialog, chat_id: str) -> int:
        cursor = repo.get_sync_cursor(self._conn, "telegram", chat_id)
        min_id = int(cursor) if cursor else 0

        # Первый синк этого чата (курсора ещё нет) — не тянем всю историю с начала
        # времён, а ограничиваемся последними N месяцами (config: telegram.initial_history_months).
        iter_kwargs: dict = {"reverse": True}
        if min_id:
            iter_kwargs["min_id"] = min_id
        else:
            cutoff = datetime.now(timezone.utc) - timedelta(days=30 * self._initial_history_months)
            iter_kwargs["offset_date"] = cutoff

        count = 0
        last_id = min_id
        async for message in self._client.iter_messages(dialog, **iter_kwargs):
            if not message.text:
                continue

            sender = await message.get_sender()
            sender_name = (
                getattr(sender, "first_name", None)
                or getattr(sender, "title", None)
                or getattr(sender, "username", None)
                or "unknown"
            )

            inserted = repo.save_message(
                self._conn,
                source="telegram",
                chat_id=chat_id,
                msg_id=str(message.id),
                sender=sender_name,
                sender_is_me=bool(message.out),
                subject=None,
                text=message.text,
                sent_at=message.date.isoformat(),
            )
            if inserted:
                count += 1
            last_id = max(last_id, message.id)

        if last_id > min_id:
            repo.set_sync_cursor(self._conn, "telegram", chat_id, str(last_id))

        return count
