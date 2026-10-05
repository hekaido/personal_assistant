"""Веб-UI для выбора отслеживаемых диалогов TG — удобнее текстового списка на 1000+ строк.

Запуск:
    .venv/bin/python scripts/select_dialogs_ui.py
Откроет локальный сервер на http://127.0.0.1:8765 — открой в браузере, отметь
диалоги галочками (с поиском по имени), нажми "Сохранить". Ctrl-C — остановить.
"""
import asyncio
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from telethon import TelegramClient

from assistant.config import load_config, load_secrets
from assistant.db import repo
from assistant.db.connection import connect, init_schema
from assistant.ingestion.telegram_ingestor import classify_chat_type

HOST, PORT = "127.0.0.1", 8765
AVATAR_CONCURRENCY = 8


async def _download_avatar(client, entity, chat_id: str, avatars_dir: Path, semaphore: asyncio.Semaphore) -> None:
    path = avatars_dir / f"{chat_id}.jpg"
    if path.exists():
        return  # уже скачана раньше — не дёргаем Telegram заново
    async with semaphore:
        try:
            await client.download_profile_photo(entity, file=str(path), download_big=False)
        except Exception:
            pass  # нет фото / приватность / любая другая причина — просто не будет картинки


async def discover(config, secrets, conn, avatars_dir: Path) -> None:
    avatars_dir.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(
        str(config.telegram_session_path), secrets.telegram_api_id, secrets.telegram_api_hash
    )
    await client.start()

    semaphore = asyncio.Semaphore(AVATAR_CONCURRENCY)
    avatar_tasks = []

    async for dialog in client.iter_dialogs():
        chat_type = classify_chat_type(dialog.entity)
        if chat_type == "channel":
            continue
        chat_id = str(dialog.id)
        last_message_at = dialog.date.isoformat() if dialog.date else None
        repo.upsert_dialog(conn, chat_id, dialog.name or chat_id, chat_type, is_new=0, last_message_at=last_message_at)
        avatar_tasks.append(_download_avatar(client, dialog.entity, chat_id, avatars_dir, semaphore))

    print(f"Скачиваю аватарки ({len(avatar_tasks)} диалогов, уже закэшированные пропускаются)...")
    await asyncio.gather(*avatar_tasks)

    await client.disconnect()


PAGE_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Выбор диалогов</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, system-ui, sans-serif; max-width: 720px; margin: 0 auto; padding: 16px; }
  h1 { font-size: 18px; }
  #search { width: 100%; padding: 10px; font-size: 15px; box-sizing: border-box; margin-bottom: 8px; }
  .toolbar { display: flex; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; align-items: center; }
  .toolbar button { padding: 6px 10px; font-size: 13px; cursor: pointer; }
  #count { font-size: 13px; opacity: 0.7; margin-left: auto; }
  ul { list-style: none; padding: 0; margin: 0; max-height: 70vh; overflow-y: auto; border: 1px solid #8884; border-radius: 8px; }
  li { display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-bottom: 1px solid #8882; }
  li:last-child { border-bottom: none; }
  li:hover { background: #80808014; }
  .avatar-wrap { position: relative; width: 32px; height: 32px; flex: none; }
  .avatar-fallback { position: absolute; inset: 0; border-radius: 50%; background: #6b7280;
                      color: white; font-size: 13px; font-weight: 600; display: flex;
                      align-items: center; justify-content: center; text-transform: uppercase; }
  .avatar-img { position: relative; width: 32px; height: 32px; border-radius: 50%; object-fit: cover; display: block; }
  .badge { font-size: 11px; padding: 2px 6px; border-radius: 4px; background: #8883; min-width: 46px; text-align: center; }
  .title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #save { position: sticky; bottom: 0; width: 100%; padding: 14px; font-size: 15px; font-weight: 600;
          background: #2563eb; color: white; border: none; border-radius: 8px; cursor: pointer; margin-top: 10px; }
  #save:active { background: #1d4ed8; }
  #status { text-align: center; font-size: 13px; margin-top: 8px; min-height: 18px; }
</style>
</head>
<body>
  <h1>Какие диалоги отслеживать?</h1>
  <input id="search" type="text" placeholder="Поиск по имени...">
  <div class="toolbar">
    <button onclick="setFiltered(true)">Выбрать все видимые</button>
    <button onclick="setFiltered(false)">Снять все видимые</button>
    <span id="count"></span>
  </div>
  <ul id="list"></ul>
  <button id="save" onclick="save()">Сохранить</button>
  <div id="status"></div>

<script>
const DIALOGS = __DIALOGS_JSON__;
const state = new Map(DIALOGS.map(d => [d.chat_id, d.tracked]));

function render() {
  const q = document.getElementById('search').value.trim().toLowerCase();
  const list = document.getElementById('list');
  list.innerHTML = '';
  let visibleCount = 0;
  for (const d of DIALOGS) {
    if (q && !d.title.toLowerCase().includes(q)) continue;
    visibleCount++;
    const li = document.createElement('li');
    const checked = state.get(d.chat_id) ? 'checked' : '';
    const initial = (d.title.trim()[0] || '?');
    li.innerHTML = `
      <input type="checkbox" ${checked} onchange="toggle('${d.chat_id}', this.checked)">
      <div class="avatar-wrap">
        <div class="avatar-fallback">${initial}</div>
        <img class="avatar-img" src="/avatar/${d.chat_id}" loading="lazy"
             onerror="this.style.display='none'">
      </div>
      <span class="badge">${d.chat_type}</span>
      <span class="title">${d.title}</span>`;
    list.appendChild(li);
  }
  const totalChecked = [...state.values()].filter(Boolean).length;
  document.getElementById('count').textContent = `Показано: ${visibleCount} · Выбрано всего: ${totalChecked}`;
}

function toggle(chatId, checked) {
  state.set(chatId, checked);
  render();
}

function setFiltered(value) {
  const q = document.getElementById('search').value.trim().toLowerCase();
  for (const d of DIALOGS) {
    if (!q || d.title.toLowerCase().includes(q)) state.set(d.chat_id, value);
  }
  render();
}

document.getElementById('search').addEventListener('input', render);

async function save() {
  const status = document.getElementById('status');
  status.textContent = 'Сохраняю...';
  const tracked_ids = [...state.entries()].filter(([, v]) => v).map(([k]) => k);
  const resp = await fetch('/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({tracked_ids})
  });
  if (resp.ok) {
    status.textContent = `Сохранено: отслеживается ${tracked_ids.length} диалогов. Можно закрыть вкладку.`;
  } else {
    status.textContent = 'Ошибка сохранения.';
  }
}

render();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    conn = None  # выставляется снаружи перед стартом сервера
    avatars_dir: Path = None

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/avatar/"):
            self._serve_avatar(self.path.removeprefix("/avatar/"))
            return
        if self.path != "/":
            self.send_response(404)
            self.end_headers()
            return
        rows = repo.get_selectable_dialogs(self.conn)
        dialogs = [
            {
                "chat_id": r["chat_id"],
                "title": r["chat_title"],
                "chat_type": r["chat_type"],
                "tracked": bool(r["tracked"]),
            }
            for r in rows
        ]
        html = PAGE_TEMPLATE.replace("__DIALOGS_JSON__", json.dumps(dialogs, ensure_ascii=False))
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_avatar(self, chat_id: str) -> None:
        if not chat_id.lstrip("-").isdigit():
            self.send_response(400)
            self.end_headers()
            return
        path = self.avatars_dir / f"{chat_id}.jpg"
        if not path.is_file():
            self.send_response(404)
            self.end_headers()
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/save":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length))
        tracked_ids = set(payload.get("tracked_ids", []))

        all_ids = {r["chat_id"] for r in repo.get_selectable_dialogs(self.conn)}
        for chat_id in all_ids:
            repo.set_tracked(self.conn, chat_id, chat_id in tracked_ids)

        body = json.dumps({"ok": True, "tracked_count": len(tracked_ids)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    config = load_config()
    secrets = load_secrets()

    conn = connect(config.db_path)
    init_schema(conn)

    avatars_dir = config.db_path.parent / "avatars"

    print("Обновляю список диалогов из Telegram...")
    asyncio.run(discover(config, secrets, conn, avatars_dir))

    Handler.conn = conn
    Handler.avatars_dir = avatars_dir
    server = HTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print(f"\nОткрой в браузере: {url}")
    print("Ctrl-C — остановить сервер, когда закончишь и нажмёшь 'Сохранить'.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()


if __name__ == "__main__":
    main()
