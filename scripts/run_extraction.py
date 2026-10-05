"""Extraction-пайплайн v2: каждый чат обрабатывается отдельно.

Для каждого чата с необработанными сообщениями:
  1. Сообщения режутся на батчи (BATCH_SIZE), каждый батч суммаризируется отдельно.
  2. Саммари батчей вливаются в дневное саммари чата (с учётом уже накопленного за сегодня).
  3. Из дневного саммари извлекается список инсайтов (дедлайны/договорённости/факты).
  4. Каждый инсайт проходит дедупликацию (векторный поиск похожих + LLM решает
     new/update/duplicate) прежде чем попасть в БД.
  5. И само дневное саммари, и все инсайты индексируются в embeddings для RAG-чата.

Запуск:
    .venv/bin/python scripts/run_extraction.py --limit-per-chat 50
    .venv/bin/python scripts/run_extraction.py --since-days 7
"""
import argparse
import asyncio
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assistant.config import load_config, load_secrets
from assistant.db import repo
from assistant.db.connection import connect, init_schema
from assistant.extraction.dedup import resolve_and_save
from assistant.extraction.insight_extractor import extract_insights
from assistant.extraction.summarizer import combine_daily_summary, summarize_batch
from assistant.llm.factory import LLMFactory

BATCH_SIZE = 20
CHAT_CONCURRENCY = 5  # сколько чатов обрабатываем параллельно
LLM_CONCURRENCY = 8   # глобальный лимит одновременных LLM-вызовов (суммаризация + дедуп)


async def process_day(
    conn, chat_id: str, chat_title: str, date_key: str, day_messages: list,
    summarizer, insight_provider, embedder, decider, similarity_threshold: float,
    llm_semaphore: asyncio.Semaphore, result: dict,
) -> None:
    """Обрабатывает сообщения ОДНОГО чата за ОДИН календарный день (по sent_at, не по dat запуска)."""
    batches = [day_messages[i : i + BATCH_SIZE] for i in range(0, len(day_messages), BATCH_SIZE)]

    async def _summarize(batch) -> str | None:
        batch_input = [{"sender": m["sender"] or "?", "text": m["text"], "sent_at": m["sent_at"]} for m in batch]
        async with llm_semaphore:
            try:
                return await summarize_batch(summarizer, chat_title, batch_input)
            except Exception as e:
                print(f"  [{chat_title} {date_key}] ошибка суммаризации батча: {e}")
                return None

    raw_summaries = await asyncio.gather(*[_summarize(b) for b in batches])
    batch_summaries = [s for s in raw_summaries if s and "ничего важного" not in s.lower()]
    if not batch_summaries:
        return

    existing = repo.get_daily_summary(conn, chat_id, date_key)
    existing_content = existing["content"] if existing else None

    async with llm_semaphore:
        daily_summary = await combine_daily_summary(summarizer, chat_title, existing_content, batch_summaries)
    summary_id = repo.save_daily_summary(conn, chat_id, date_key, daily_summary)

    summary_vector = await embedder.embed(daily_summary)
    repo.upsert_embedding(conn, "summary", summary_id, summary_vector)

    async with llm_semaphore:
        insights = await extract_insights(insight_provider, chat_title, daily_summary, date_key)

    last_message_id = day_messages[-1]["id"]
    occurred_at = day_messages[-1]["sent_at"]
    for item in insights:
        async with llm_semaphore:
            try:
                outcome = await resolve_and_save(
                    conn, embedder, decider, item, chat_id, last_message_id, summary_id,
                    similarity_threshold, occurred_at,
                )
                result[outcome] = result.get(outcome, 0) + 1
                print(f"  [{chat_title} {date_key}] [{outcome}] {item.type}: {item.description}")
            except Exception as e:
                print(f"  [{chat_title} {date_key}] ошибка dedup/save: {e}")


async def process_chat(
    conn, chat_id: str, chat_title: str, limit_per_chat: int | None, since_iso: str | None,
    summarizer, insight_provider, embedder, decider, similarity_threshold: float,
    llm_semaphore: asyncio.Semaphore,
) -> dict:
    messages = repo.get_unprocessed_messages_for_chat(conn, chat_id, limit=limit_per_chat, since_iso=since_iso)
    if not messages:
        return {"chat": chat_title, "messages": 0}

    result = {"chat": chat_title, "messages": len(messages), "new": 0, "updated": 0, "duplicate": 0}

    # Группируем по КАЛЕНДАРНОЙ ДАТЕ САМОГО СООБЩЕНИЯ (sent_at), а не по дате запуска —
    # иначе при обработке старого бэклога полгода истории схлопнутся в одно "сегодняшнее"
    # саммари одним гигантским вызовом (см. известное ограничение, найденное на "Лега Трио").
    by_date: dict[str, list] = defaultdict(list)
    for m in messages:
        by_date[m["sent_at"][:10]].append(m)

    for date_key in sorted(by_date):
        await process_day(
            conn, chat_id, chat_title, date_key, by_date[date_key],
            summarizer, insight_provider, embedder, decider, similarity_threshold,
            llm_semaphore, result,
        )

    repo.mark_processed(conn, [m["id"] for m in messages])
    return result


async def main(limit_per_chat: int | None, since_days: int | None) -> None:
    config = load_config()
    secrets = load_secrets()

    conn = connect(config.db_path)
    init_schema(conn)

    factory = LLMFactory(config, secrets)
    summarizer = factory.get("summarize")
    insight_provider = factory.get("extraction")
    decider = factory.get("extraction")
    embedder = factory.get("embeddings")

    since_iso = None
    if since_days is not None:
        since_iso = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()

    chat_ids = repo.get_chats_with_unprocessed(conn, since_iso=since_iso)
    if not chat_ids:
        print("Нет чатов с необработанными сообщениями" + (f" за последние {since_days} дн." if since_days else "") + ".")
        return

    titles = {}
    for cid in chat_ids:
        row = conn.execute("SELECT chat_title FROM tracked_dialogs WHERE chat_id = ?", (cid,)).fetchone()
        titles[cid] = row["chat_title"] if row else cid

    print(f"Обрабатываю {len(chat_ids)} чатов (конкурентность чатов={CHAT_CONCURRENCY}, LLM={LLM_CONCURRENCY})...\n")

    llm_semaphore = asyncio.Semaphore(LLM_CONCURRENCY)
    chat_semaphore = asyncio.Semaphore(CHAT_CONCURRENCY)

    async def _process(cid: str) -> dict:
        async with chat_semaphore:
            return await process_chat(
                conn, cid, titles[cid], limit_per_chat, since_iso,
                summarizer, insight_provider, embedder, decider,
                config.dedup.similarity_threshold, llm_semaphore,
            )

    results = await asyncio.gather(*[_process(cid) for cid in chat_ids])

    total_messages = sum(r.get("messages", 0) for r in results)
    total_new = sum(r.get("new", 0) for r in results)
    total_updated = sum(r.get("updated", 0) for r in results)
    total_dup = sum(r.get("duplicate", 0) for r in results)
    print(
        f"\nГотово: {len(chat_ids)} чатов, {total_messages} сообщений, "
        f"{total_new} новых инсайтов, {total_updated} обновлено, {total_dup} дублей отсеяно."
    )
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--limit-per-chat", type=int, default=None,
        help="Максимум необработанных сообщений на чат за этот запуск (по умолчанию — все)",
    )
    parser.add_argument(
        "--since-days", type=int, default=None,
        help="Брать только сообщения не старше N дней",
    )
    args = parser.parse_args()
    asyncio.run(main(args.limit_per_chat, args.since_days))
