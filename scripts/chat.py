"""Интерактивный CLI для проверки RAG-чата по базе знаний, без полноценного бота.

Запуск:
    .venv/bin/python scripts/chat.py
    .venv/bin/python scripts/chat.py "когда встреча с Мохом?"
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assistant.config import load_config, load_secrets
from assistant.db.connection import connect, init_schema
from assistant.features.chat_kb import answer_question
from assistant.llm.factory import LLMFactory


async def main() -> None:
    config = load_config()
    secrets = load_secrets()
    conn = connect(config.db_path)
    init_schema(conn)

    factory = LLMFactory(config, secrets)
    embedder = factory.get("embeddings")
    chat_provider = factory.get("chat")

    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        answer = await answer_question(conn, embedder, chat_provider, question)
        print(answer)
        conn.close()
        return

    print("RAG-чат по базе знаний. Пустая строка или Ctrl-C — выход.\n")
    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            break
        answer = await answer_question(conn, embedder, chat_provider, question)
        print(f"\n{answer}\n")

    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
