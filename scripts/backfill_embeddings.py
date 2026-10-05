"""Индексирует уже существующие commitments/facts, у которых ещё нет эмбеддинга.

Нужен один раз после перехода на новый extraction-пайплайн с дедупликацией —
записи, сохранённые старым пайплайном, никогда не проходили через embeddings,
и без этого дедуп их просто не увидит как кандидатов на сравнение.

Запуск:
    .venv/bin/python scripts/backfill_embeddings.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assistant.config import load_config, load_secrets
from assistant.db import repo
from assistant.db.connection import connect, init_schema
from assistant.llm.factory import LLMFactory


def _already_indexed(conn, entity_type: str) -> set[int]:
    rows = conn.execute("SELECT entity_id FROM embeddings WHERE entity_type = ?", (entity_type,)).fetchall()
    return {r["entity_id"] for r in rows}


async def main() -> None:
    config = load_config()
    secrets = load_secrets()
    conn = connect(config.db_path)
    init_schema(conn)

    embedder = LLMFactory(config, secrets).get("embeddings")

    indexed_commitments = _already_indexed(conn, "commitment")
    indexed_facts = _already_indexed(conn, "fact")

    commitments = conn.execute("SELECT id, description FROM commitments").fetchall()
    facts = conn.execute("SELECT id, fact_text FROM facts").fetchall()

    n = 0
    for row in commitments:
        if row["id"] in indexed_commitments:
            continue
        vector = await embedder.embed(row["description"])
        repo.upsert_embedding(conn, "commitment", row["id"], vector)
        n += 1

    for row in facts:
        if row["id"] in indexed_facts:
            continue
        vector = await embedder.embed(row["fact_text"])
        repo.upsert_embedding(conn, "fact", row["id"], vector)
        n += 1

    print(f"Проиндексировано {n} новых записей (commitments+facts).")
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
