"""Дедупликация инсайтов: векторный поиск похожих + LLM решает new/update/duplicate.

Работает по двум колбэкам-эмбеддерам и решателям, не по конкретному провайдеру —
эмбеддинги считает LLMProvider с .embed() (локальная модель), решение принимает
LLMProvider с .complete() (может быть другая модель/провайдер).
"""
import math

from assistant.db import repo
from assistant.extraction.schemas import ExtractedItem
from assistant.llm.base import LLMProvider, Message, ToolDef

DECISION_TOOL = ToolDef(
    name="resolve_duplicate",
    description=(
        "Реши, является ли новый инсайт дубликатом/обновлением одного из похожих "
        "существующих, или это действительно что-то новое."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["new", "update", "duplicate"]},
            "existing_id": {
                "type": ["integer", "null"],
                "description": "id существующей записи из списка кандидатов, если action не 'new'",
            },
            "reason": {"type": "string", "description": "Краткое объяснение решения"},
        },
        "required": ["action", "reason"],
    },
)

DECISION_SYSTEM_PROMPT = """Ты решаешь, дублирует ли новый инсайт что-то уже сохранённое.

- "new" — это действительно новая, самостоятельная информация
- "update" — это та же договорённость/встреча, что и один из кандидатов, но с обновлением \
(например, перенесли дату, изменился статус) — тогда existing_id обязателен
- "duplicate" — это буквально то же самое, что уже есть, без новой информации — existing_id обязателен

Сравнивай по смыслу, а не по дословному совпадению текста."""


def _cosine_to_l2_distance(cosine_threshold: float) -> float:
    """Векторы из local-эмбеддера нормализованы (unit length), поэтому для них
    L2_distance² = 2 - 2*cos_sim — отсюда можно перевести порог косинусной похожести
    (как он задан в config.yaml) в максимальную L2-дистанцию для фильтрации в sqlite-vec."""
    return math.sqrt(max(0.0, 2 - 2 * cosine_threshold))


def _candidate_text(conn, entity_type: str, entity_id: int) -> tuple[str, str] | None:
    """Возвращает (описание, статус-текст) для кандидата, или None если не нашли."""
    if entity_type == "commitment":
        row = repo.get_commitment(conn, entity_id)
        if row is None:
            return None
        return row["description"], f"due_at={row['due_at']}, status={row['status']}"
    if entity_type == "fact":
        row = repo.get_fact(conn, entity_id)
        if row is None:
            return None
        return row["fact_text"], "факт"
    return None


async def resolve_and_save(
    conn,
    embedder: LLMProvider,
    decider: LLMProvider,
    item: ExtractedItem,
    chat_id: str,
    message_id: int,
    source_summary_id: int,
    similarity_threshold: float,
    occurred_at: str,
    top_k: int = 5,
) -> str:
    """Сохраняет инсайт с учётом дедупликации. Возвращает 'new' | 'updated' | 'duplicate'.

    occurred_at — sent_at сообщения-источника (не момент запуска extraction) — на нём строятся
    created_at/updated_at, чтобы "что изменилось вчера" отражало реальные события переписки."""
    entity_type = "fact" if item.type == "fact" else "commitment"

    vector = await embedder.embed(item.description)
    max_distance = _cosine_to_l2_distance(similarity_threshold)

    candidates = repo.query_similar_embeddings(conn, ["commitment", "fact"], vector, top_k)
    close_candidates = [c for c in candidates if c["distance"] <= max_distance]

    if not close_candidates:
        new_id = _insert(conn, item, chat_id, message_id, source_summary_id, occurred_at)
        repo.upsert_embedding(conn, entity_type, new_id, vector)
        return "new"

    candidate_lines = []
    valid_candidates = []
    for c in close_candidates:
        info = _candidate_text(conn, c["entity_type"], c["entity_id"])
        if info is None:
            continue
        desc, extra = info
        candidate_lines.append(f"id={c['entity_id']} [{c['entity_type']}]: {desc} ({extra})")
        valid_candidates.append(c)

    if not candidate_lines:
        new_id = _insert(conn, item, chat_id, message_id, source_summary_id, occurred_at)
        repo.upsert_embedding(conn, entity_type, new_id, vector)
        return "new"

    prompt = (
        f"Новый инсайт: [{item.type}] {item.description} (due_at={item.due_at})\n\n"
        f"Похожие существующие записи:\n" + "\n".join(candidate_lines)
    )
    response = await decider.complete(
        messages=[Message(role="user", text=prompt)],
        system=DECISION_SYSTEM_PROMPT,
        tools=[DECISION_TOOL],
        max_tokens=512,
    )

    decision = None
    for tc in response.tool_calls:
        if tc.name == "resolve_duplicate":
            decision = tc.input
            break

    if decision is None or decision.get("action") == "new":
        new_id = _insert(conn, item, chat_id, message_id, source_summary_id, occurred_at)
        repo.upsert_embedding(conn, entity_type, new_id, vector)
        return "new"

    existing_id = decision.get("existing_id")
    matched = next((c for c in valid_candidates if c["entity_id"] == existing_id), None)

    if decision["action"] == "duplicate" and matched:
        return "duplicate"

    if decision["action"] == "update" and matched:
        if matched["entity_type"] == "commitment":
            repo.update_commitment(
                conn, existing_id, occurred_at,
                description=item.description, due_at=item.due_at, due_confidence=item.due_confidence,
            )
        else:
            repo.update_fact(conn, existing_id, item.description)
        repo.upsert_embedding(conn, matched["entity_type"], existing_id, vector)
        return "updated"

    # Решение указало на action без валидного existing_id — считаем новым, чтобы не терять данные.
    new_id = _insert(conn, item, chat_id, message_id, source_summary_id, occurred_at)
    repo.upsert_embedding(conn, entity_type, new_id, vector)
    return "new"


def _insert(conn, item: ExtractedItem, chat_id: str, message_id: int, source_summary_id: int, occurred_at: str) -> int:
    if item.type == "fact":
        return repo.insert_fact(
            conn, message_id, subject=chat_id, fact_text=item.description, tags=[], occurred_at=occurred_at,
            chat_id=chat_id, source_summary_id=source_summary_id,
        )
    return repo.insert_commitment(
        conn, message_id, item.type, item.description, item.due_at, item.due_confidence, item.participants,
        occurred_at=occurred_at, chat_id=chat_id, source_summary_id=source_summary_id,
    )
