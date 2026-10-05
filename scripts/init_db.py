"""Инициализация БД: создаёт data/assistant.db со всеми таблицами."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assistant.config import load_config
from assistant.db.connection import connect, init_schema


def main() -> None:
    config = load_config()
    conn = connect(config.db_path)
    init_schema(conn)
    conn.close()
    print(f"БД инициализирована: {config.db_path}")


if __name__ == "__main__":
    main()
