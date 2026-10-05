from typing import Protocol


class Ingestor(Protocol):
    async def sync(self) -> int:
        """Забирает новые сообщения из источника, возвращает число реально новых (не дублей)."""
        ...
