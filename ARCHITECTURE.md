# Личный ассистент — архитектура MVP

## 0. Скоуп MVP

Функционал:
1. **Бриф дня** — утром: дедлайны на сегодня, что изменилось/произошло за вчера, план на день.
2. **Чат-бот как база знаний** — спросить в Telegram про что угодно из истории переписок/почты, получить ответ с опорой на реальные сообщения.

Источники извлечения: **Telegram** (личный аккаунт, Telethon) + **Gmail** (OAuth API).

Хранение: полностью локально (SQLite-файл на диске). В облако уходят только точечные вызовы к LLM API (текст конкретных сообщений/вопросов), ничего не персистится на стороне продукта вне твоей машины.

Рантайм: процесс работает на ноутбуке/десктопе, пока он включён (фоновый демон + планировщик).

---

## 1. Структура проекта

```
personal-assistant/
├── pyproject.toml
├── .env                          # секреты (в .gitignore)
├── .env.example
├── config.yaml                   # несекретная конфигурация
├── data/
│   ├── assistant.db               # SQLite (сырые данные + факты + вектора)
│   └── telegram.session           # сессия Telethon
├── src/
│   └── assistant/
│       ├── __init__.py
│       ├── config.py               # pydantic-settings: загрузка config.yaml + .env
│       ├── db/
│       │   ├── schema.sql
│       │   ├── models.py           # SQLAlchemy/Pydantic модели строк
│       │   └── repo.py             # CRUD-функции поверх SQLite
│       ├── ingestion/
│       │   ├── base.py             # Protocol Ingestor
│       │   ├── telegram_ingestor.py
│       │   └── gmail_ingestor.py
│       ├── extraction/
│       │   ├── schemas.py          # Pydantic: Commitment, Event, Fact
│       │   ├── filter.py           # дешёвый LLM-фильтр "важно/не важно"
│       │   ├── extractor.py        # structured extraction через LLM
│       │   └── dedup.py            # сверка с существующими записями
│       ├── llm/
│       │   ├── base.py             # Protocol LLMProvider
│       │   ├── claude_provider.py
│       │   ├── openai_provider.py
│       │   ├── local_embeddings.py # sentence-transformers
│       │   └── factory.py          # выбор провайдера по config.yaml
│       ├── features/
│       │   ├── daily_brief.py      # сборка и генерация брифа
│       │   └── chat_kb.py          # RAG-чат по базе знаний
│       ├── bot/
│       │   ├── telegram_bot.py     # python-telegram-bot: команды, входящие вопросы
│       │   └── formatters.py       # красивый markdown для Telegram
│       ├── scheduler/
│       │   └── jobs.py             # APScheduler: sync-джобы, brief-джоба
│       └── main.py                 # точка входа, поднимает всё вместе
├── scripts/
│   ├── init_db.py
│   ├── telegram_auth.py            # первичная авторизация Telethon (интерактивно)
│   ├── select_dialogs.py           # выбор отслеживаемых диалогов TG (онбординг + повторный запуск)
│   └── gmail_auth.py               # первичный OAuth Gmail
└── tests/
    ├── test_extraction.py
    ├── test_dedup.py
    └── test_chat_kb.py
```

---

## 2. Схема БД (SQLite + sqlite-vec)

```sql
-- ── Сырые данные ──────────────────────────────────────────────

CREATE TABLE messages (
    id              INTEGER PRIMARY KEY,
    source          TEXT NOT NULL CHECK (source IN ('telegram','gmail')),
    source_chat_id  TEXT NOT NULL,      -- chat_id / thread_id
    source_msg_id   TEXT NOT NULL,      -- id сообщения в источнике
    sender          TEXT,
    sender_is_me    INTEGER NOT NULL DEFAULT 0,
    subject         TEXT,               -- для gmail (тема письма)
    text            TEXT NOT NULL,
    sent_at         TEXT NOT NULL,      -- ISO8601
    ingested_at     TEXT NOT NULL,
    processed_at    TEXT,               -- когда прошло extraction; NULL = ещё не обработано
    UNIQUE(source, source_chat_id, source_msg_id)
);
CREATE INDEX idx_messages_processed ON messages(processed_at);
CREATE INDEX idx_messages_sent_at ON messages(sent_at);

-- ── Извлечённые сущности ──────────────────────────────────────

CREATE TABLE commitments (
    id                  INTEGER PRIMARY KEY,
    message_id          INTEGER NOT NULL REFERENCES messages(id),
    type                TEXT NOT NULL CHECK (type IN ('deadline','promise','meeting','task')),
    description         TEXT NOT NULL,
    due_at              TEXT,                 -- ISO8601, может быть NULL если срок неясен
    due_confidence      TEXT NOT NULL CHECK (due_confidence IN ('exact','approximate','unclear')),
    status              TEXT NOT NULL DEFAULT 'open'
                            CHECK (status IN ('open','done','cancelled','overdue')),
    participants        TEXT,             -- JSON-массив имён
    confirmed_by_user   INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX idx_commitments_due ON commitments(due_at);
CREATE INDEX idx_commitments_status ON commitments(status);

CREATE TABLE facts (
    id            INTEGER PRIMARY KEY,
    message_id    INTEGER NOT NULL REFERENCES messages(id),
    subject       TEXT NOT NULL,        -- о ком/о чём факт
    fact_text     TEXT NOT NULL,
    tags          TEXT,                 -- JSON-массив
    created_at    TEXT NOT NULL
);

-- ── Векторные эмбеддинги (sqlite-vec) ─────────────────────────
-- Единая таблица эмбеддингов для семантического поиска
-- по messages и facts одновременно (RAG для чата)

CREATE VIRTUAL TABLE embeddings USING vec0(
    entity_type   TEXT,      -- 'message' | 'fact'
    entity_id     INTEGER,
    embedding     FLOAT[1024]   -- размерность зависит от модели (bge-m3 = 1024)
);

-- ── Отслеживаемые диалоги TG ───────────────────────────────────

CREATE TABLE tracked_dialogs (
    chat_id         TEXT PRIMARY KEY,      -- Telegram dialog id
    chat_title      TEXT NOT NULL,
    chat_type       TEXT NOT NULL CHECK (chat_type IN ('user','group','channel','bot')),
    tracked         INTEGER NOT NULL DEFAULT 0,   -- 0/1 — включено ли извлечение из этого диалога
    is_new          INTEGER NOT NULL DEFAULT 0,   -- появился уже после онбординга, ждёт решения
    added_at        TEXT NOT NULL,
    decided_at      TEXT                          -- когда пользователь принял решение tracked/not
);
CREATE INDEX idx_tracked_dialogs_tracked ON tracked_dialogs(tracked);

-- ── Служебные таблицы ──────────────────────────────────────────

CREATE TABLE sync_state (
    source          TEXT PRIMARY KEY,     -- 'telegram' | 'gmail'
    last_cursor     TEXT,                 -- offset_id / historyId
    last_synced_at  TEXT
);

CREATE TABLE reminders_sent (
    id              INTEGER PRIMARY KEY,
    commitment_id   INTEGER NOT NULL REFERENCES commitments(id),
    sent_at         TEXT NOT NULL,
    channel         TEXT NOT NULL DEFAULT 'telegram'
);

CREATE TABLE llm_calls_log (
    id            INTEGER PRIMARY KEY,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    purpose       TEXT NOT NULL,   -- 'filter' | 'extract' | 'chat' | 'brief' | 'embed'
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd      REAL,
    created_at    TEXT NOT NULL
);

CREATE TABLE daily_briefs (
    id            INTEGER PRIMARY KEY,
    brief_date    TEXT NOT NULL UNIQUE,   -- YYYY-MM-DD
    content       TEXT NOT NULL,          -- итоговый markdown-текст
    generated_at  TEXT NOT NULL
);
```

Почему так:
- `tracked_dialogs` — источник правды о том, какие TG-диалоги вообще участвуют в системе. Хранится в БД, а не в `config.yaml`, потому что список диалогов персональный, большой (десятки-сотни чатов) и меняется сам по себе (появляются новые собеседники) — это данные, а не конфигурация кода.
- `is_new` — диалог, появившийся после первичного онбординга (новый собеседник написал впервые); пока пользователь не решил, отслеживать его или нет, ingestion такой диалог пропускает.
- `chat_type = 'channel'` — каналы по умолчанию не участвуют ни в извлечении, ни тем более в брифе/RAG-чате: это не переписка, а вещание (новости, паблики, рассылки), в ней не бывает личных договорённостей и дедлайнов. Onboarding вообще не предлагает их выбирать — `tracked` для каналов всегда `0`, если явно не переключено вручную.
- `messages` — единый источник правды для сырых данных из обоих каналов, дальше всё строится поверх.
- `processed_at IS NULL` — простая очередь на extraction, без брокера сообщений.
- `due_confidence` — явно фиксируем неуверенность LLM, чтобы UI/бот мог по-разному показывать "точный дедлайн" и "что-то на следующей неделе, уточни".
- `embeddings` как отдельная vec0-таблица, а не колонка — так проще индексировать и не раздувает основные таблицы.
- `llm_calls_log` — с первого дня видно, сколько тратится и на что, это пригодится и для оптимизации, и для будущего продукта (usage-based pricing).

---

## 3. Пайплайн ingestion

### Telegram (`telegram_ingestor.py`)
- Авторизация один раз через `scripts/telegram_auth.py` (номер телефона + код), сессия сохраняется в `data/telegram.session`.
- Перед первой синхронизацией — **онбординг выбора диалогов** (см. раздел 3.1): без этого шага ingestion не запускается, чтобы не утащить в БД вообще всё подряд с первого дня.
- Периодическая джоба (например, раз в 15 минут) через Telethon, но **только по диалогам, отмеченным `tracked=1`** в `tracked_dialogs`:
  ```python
  tracked = repo.get_tracked_dialog_ids()   # WHERE tracked = 1
  async for dialog in client.iter_dialogs():
      if dialog.id not in tracked:
          register_if_new(dialog)           # новый диалог → is_new=1, tracked=0, ждёт решения
          continue
      async for message in client.iter_messages(dialog, offset_id=last_cursor, reverse=True):
          save_to_messages(...)
  ```
- Курсор (`sync_state.last_cursor` per чат) не даёт перечитывать всю историю каждый раз.
- **Первый запуск** — отдельная логика полной синхронизации истории (с ограничением, например последние 6 месяцев, чтобы не тащить гигабайты сразу), и только по уже выбранным диалогам.
- Каналы (`chat_type='channel'`) в онбординге не показываются вовсе и всегда остаются `tracked=0` — см. 3.1.

### 3.1 Онбординг: выбор отслеживаемых диалогов

`scripts/select_dialogs.py`, запускается один раз после `telegram_auth.py` и вручную повторно, если нужно поменять список.

1. Тянем список диалогов через `client.iter_dialogs()`, **сразу отфильтровав `chat_type == 'channel'`** — каналы никогда не предлагаются к выбору, это вещание, а не переписка.
2. Оставшиеся (личные чаты + группы) сохраняем в `tracked_dialogs` с `tracked=0` по умолчанию.
3. Показываем пользователю список для выбора. Два варианта UX:
   - **CLI** (проще для MVP): нумерованный список в терминале, пользователь вводит индексы через запятую (`1,3,7-9`) или `all`.
   - **Telegram-бот с inline-кнопками** (удобнее, задел на будущее для продукта): бот присылает список чатов постранично с чекбоксами `✅/⬜`, пользователь кликает, нажимает "Готово".
4. Выбранные помечаются `tracked=1`, `decided_at=now`.
5. **Новые диалоги после онбординга**: если во время работы появляется сообщение из диалога, которого нет в `tracked_dialogs` (новый собеседник), ingestion создаёт запись с `is_new=1, tracked=0` и не обрабатывает сообщения из него, пока пользователь явно не включит трекинг. Раз в день бот может присылать сводку "новые диалоги, которые пока не отслеживаются" с кнопками включить/скрыть — чтобы не приходилось руками запускать `select_dialogs.py` каждый раз.
6. Команда `/dialogs` в самом Telegram-боте — в любой момент посмотреть текущий список и переключить `tracked` для любого диалога, без пересборки.

### Gmail (`gmail_ingestor.py`)
- OAuth через `scripts/gmail_auth.py`, токен обновляется автоматически (`google-auth` refresh flow), хранится локально в `data/`.
- Используем `users.history.list` с сохранённым `historyId` для инкрементальной синхронизации (эффективнее, чем перечитывать все письма).
- Достаём: тема, отправитель, тело письма (plain text, HTML → strip тегов через `beautifulsoup4`), дата.
- Фильтр по лейблам — например, только INBOX, исключить Promotions/Social категории Gmail сразу на уровне запроса.

---

## 4. Пайплайн extraction

Двухступенчатый, чтобы не гонять дорогую модель на каждое сообщение:

**Ступень 1 — дешёвый фильтр** (`filter.py`, модель типа Haiku, батчами по 20-50 сообщений за раз):
```
Prompt: "Вот список сообщений. Для каждого верни true/false —
содержит ли оно дедлайн, договорённость, встречу или важный факт
для запоминания. Игнорируй бытовой small talk."
```
Экономит вызовы дорогой модели на 80-90% сообщений (обычный чатик, мемы, подтверждения "ок").

**Ступень 2 — structured extraction** (`extractor.py`, модель посильнее, только для сообщений, прошедших фильтр):
```python
class ExtractedItem(BaseModel):
    type: Literal["deadline","promise","meeting","task","fact","none"]
    description: str
    due_at: Optional[str]          # ISO8601 или None
    due_confidence: Literal["exact","approximate","unclear"]
    participants: list[str]
```
Вызов через tool use / structured output API, результат сразу валидируется Pydantic.

**Дедупликация** (`dedup.py`):
1. Считаем эмбеддинг нового описания.
2. Ищем ближайшие существующие `commitments`/`facts` через sqlite-vec (`ORDER BY distance LIMIT 5`).
3. Если похожесть выше порога — просим LLM решить: "это апдейт существующей договорённости или новая" (например, перенос даты встречи — это update, а не дубль).
4. Если новое — INSERT, если апдейт — UPDATE существующей записи + лог изменения.

Это напрямую питает пункт брифа "что изменилось за вчера" — источник этих изменений и есть updated `commitments`.

---

## 5. Фича 1 — Бриф дня

`features/daily_brief.py`, запускается джобой APScheduler каждое утро (например 7:30, cron-триггер).

Сборка данных (без LLM, чистые SQL-запросы):
1. **Дедлайны сегодня**: `SELECT * FROM commitments WHERE due_at BETWEEN today_00:00 AND today_23:59 AND status='open'`
2. **Изменения за вчера**: `commitments`/`facts`, где `created_at` или `updated_at` попадает во вчерашний день
3. **Просроченное** (бонус): `status='open' AND due_at < now` → помечаем overdue

Генерация текста — один вызов LLM, который получает структурированные данные (не сырые сообщения) и оформляет их в связный, читаемый бриф:
```
System: Ты — личный ассистент. Сформируй короткий утренний бриф
по данным ниже. Разделы: "Сегодня", "Что изменилось вчера", "Внимание" (overdue).
Пиши по-русски, кратко, без воды.

Data: {json со списками commitments}
```
Результат сохраняется в `daily_briefs` и отправляется через Telegram-бота (`bot/telegram_bot.py`, `send_message` тебе в личку).

---

## 6. Фича 2 — Чат-бот как база знаний (RAG)

`features/chat_kb.py`, вызывается из `bot/telegram_bot.py` на любое текстовое сообщение боту, не являющееся командой.

Поток:
1. Пользователь пишет вопрос в Telegram-бот: *"когда я в последний раз обсуждал контракт с Аней?"*
2. Считаем эмбеддинг вопроса.
3. Векторный поиск в `embeddings` (top-k, например 10) по `messages` и `facts`.
4. Достаём сами тексты найденных записей из `messages`/`facts` по `entity_id`.
5. Собираем контекст + вопрос → отправляем в LLM chat с системным промптом:
   ```
   Ты отвечаешь на вопросы, опираясь ТОЛЬКО на предоставленный контекст
   из переписок пользователя. Если ответа нет в контексте — так и скажи,
   не выдумывай. Указывай, из какого источника и примерно когда была информация.
   ```
6. Опционально — **tool use**, чтобы модель сама могла расширить поиск (`search_more(query)`) или отметить договорённость выполненной (`mark_done(commitment_id)`) прямо из диалога.
7. Ответ форматируется (`bot/formatters.py`) и уходит пользователю.

Это и есть основной сценарий "база знаний" — не просто grep по тексту, а семантический поиск + LLM-синтез поверх реальных фрагментов переписки.

---

## 7. LLM-провайдер (адаптивно настраиваемый)

```python
# llm/base.py
class LLMProvider(Protocol):
    async def complete(self, messages: list[dict], tools: list[dict] = None) -> LLMResponse: ...
    async def embed(self, text: str) -> list[float]: ...
```

`config.yaml`:
```yaml
llm:
  filter:     {provider: claude, model: claude-haiku-4-5}
  extraction: {provider: claude, model: claude-haiku-4-5}
  chat:       {provider: claude, model: claude-sonnet-5}
  brief:      {provider: claude, model: claude-sonnet-5}
  embeddings: {provider: local, model: bge-m3}
```
`llm/factory.py` читает конфиг и инстанцирует нужный класс под каждую задачу — можно на лету поменять `chat.provider` на `openai` без изменения кода фич.

---

## 8. Конфиг и секреты

- `.env` — только секреты: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GMAIL_CLIENT_SECRET_PATH`.
- `config.yaml` — всё несекретное: расписание брифа, модели по задачам, пороги дедупликации. Список отслеживаемых диалогов сюда не входит — он персональный и объёмный, живёт в `tracked_dialogs` (см. 3.1).
- Загрузка через `pydantic-settings`, валидация при старте — приложение не запустится с неполным конфигом, а не упадёт посреди ночи на джобе.

---

## 9. Рантайм и планировщик

`main.py` поднимает один процесс с тремя параллельными задачами (`asyncio.gather`):
1. **Ingestion loop** — APScheduler джобы (Telegram каждые 15 мин, Gmail каждые 15 мин)
2. **Extraction loop** — джоба каждые 5-10 минут разбирает необработанные `messages`
3. **Telegram bot polling** — слушает входящие сообщения для чата
4. **Daily brief job** — cron 7:30 утра

На macOS для автозапуска при старте системы — `launchd` (plist в `~/Library/LaunchAgents/`), не cron (cron на macOS не переживает сон/перезагрузку так же удобно). Это можно оформить отдельным скриптом `scripts/install_launchd.sh`.

---

## 10. Порядок реализации (этапы)

1. **Каркас**: `config.py`, `db/schema.sql`, `init_db.py`, `llm/` (только Claude-провайдер для начала)
2. **Telegram ingestion** — авторизация + сохранение сырых сообщений в `messages`, без extraction
3. **Extraction pipeline** — фильтр + structured extraction на сохранённых сообщениях, ручная проверка качества на своих реальных чатах
4. **Gmail ingestion** — тот же паттерн, что и Telegram
5. **Дедупликация** — как только появится достаточно пересекающихся данных
6. **Daily brief** — сборка + генерация + отправка ботом (первая видимая ценность)
7. **RAG chat** — эмбеддинги, векторный поиск, чат-хендлер в боте
8. **Планировщик + автозапуск** — превращаем в постоянно работающий сервис

Шаги 6 и 7 — по сути и есть весь MVP-функционал, который ты просил; шаги 1-5 — фундамент под них.

---

## 11. Открытые вопросы для решения по ходу

- Порог похожести для дедупликации (косинусное расстояние) — подбирается эмпирически на реальных данных.
- Нужно ли подтверждение пользователем перед тем, как commitment попадёт в бриф (риск ложных срабатываний LLM на неявных формулировках).
- Глубина истории при первой синхронизации Telegram/Gmail (весь архив — долго и дорого по LLM-вызовам; последние N месяцев — практичнее для старта).
- Ретеншн сырых `messages` — хранить вечно или чистить старше N месяцев, оставляя только извлечённые `commitments`/`facts`.
