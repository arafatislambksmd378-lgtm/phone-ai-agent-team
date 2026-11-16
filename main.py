"""Phone AI Agent Team — entry point. Telegram listener + orchestrator.

Usage:
  python3 main.py            # start Telegram bot (requires .env)
  python3 main.py --demo     # run a local demo project without Telegram
  python3 main.py --status   # print DB status
"""
from __future__ import annotations
import argparse
import logging
import sys
from pathlib import Path

from config.settings import get_settings
from orchestrator.engine import OrchestratorEngine
from integrations.telegram.bot import TelegramBot, is_authorized
from security.validation import validate_upload, safe_extract_zip
from security.secret_manager import redact

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

HELP = """Phone AI Agent Team — commands:
/start — welcome
/help — this help
/new <name> — create project (then send requirements/files)
/status <project_id> — project status
/agents — list 16 agents
/pause /resume /stop <project_id> — control
/approve <id> /reject <id> — approvals
/logs <project_id> — recent logs
/report <project_id> — final report
/projects — list projects
/recover <project_id> — recover interrupted tasks
/settings — config summary (no secrets)
Send a TXT/MD/PDF/ZIP file to ingest a blueprint.
"""

class TelegramHandler:
    def __init__(self, engine: OrchestratorEngine, settings):
        self.engine = engine
        self.settings = settings
        self._current: dict[int, str] = {}  # chat_id -> project_id
        self._pending_name: dict[int, str] = {}

    def __call__(self, bot: TelegramBot, update: dict) -> None:
        if not is_authorized(update, self.settings.telegram_allowed_user_id):
            msg = update.get("message") or {}
            chat = (msg.get("chat") or {}).get("id")
            if chat:
                bot.send_message(chat, "⛔ Not authorized.")
            return
        msg = update.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        doc = msg.get("document")
        if doc:
            self._handle_file(bot, chat_id, doc)
            return
        if not text:
            return
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        if cmd == "/start":
            bot.send_message(chat_id, "👋 Phone AI Agent Team ready. " + HELP)
        elif cmd == "/help":
            bot.send_message(chat_id, HELP)
        elif cmd == "/new":
            name = arg or f"project_{chat_id}"
            pid = self.engine.create_project(name, requirements=f"Project: {name}\nAwaiting requirements...", quick=False)
            self._current[chat_id] = pid
            bot.send_message(chat_id, f"✅ Project {pid} created. Send requirements text or a file now.")
        elif cmd == "/status":
            pid = arg or self._current.get(chat_id, "")
            if not pid:
                bot.send_message(chat_id, "Usage: /status <project_id>")
            else:
                st = self.engine.status(pid)
                lines = [f"Project: {pid} — {st['lifecycle']}"]
                lines += [f"• {t['agent']}: {t['status']}" for t in st["tasks"]]
                bot.send_message(chat_id, "\n".join(lines) or "No tasks yet.")
        elif cmd == "/agents":
            from agents.registry import AGENT_NAMES
            bot.send_message(chat_id, "16 Agents:\n" + "\n".join(f"{i}. {v}" for i, v in enumerate(AGENT_NAMES.values(), 1)))
        elif cmd == "/pause":
            self.engine.pause()
            bot.send_message(chat_id, "⏸ Paused.")
        elif cmd == "/resume":
            self.engine.resume()
            bot.send_message(chat_id, "▶ Resumed.")
        elif cmd == "/stop":
            pid = arg or self._current.get(chat_id, "")
            if pid:
                self.engine.cancel(pid)
                bot.send_message(chat_id, f"🛑 Cancelled {pid}.")
        elif cmd in ("/approve", "/reject"):
            if not arg:
                bot.send_message(chat_id, f"Usage: {cmd} <approval_id>")
            else:
                if cmd == "/approve":
                    self.engine.approvals.approve(arg, by=str(chat_id))
                    bot.send_message(chat_id, f"✅ Approved {arg}.")
                else:
                    self.engine.approvals.reject(arg, by=str(chat_id))
                    bot.send_message(chat_id, f"❌ Rejected {arg}.")
        elif cmd == "/logs":
            pid = arg or self._current.get(chat_id, "")
            if not pid:
                bot.send_message(chat_id, "Usage: /logs <project_id>")
            else:
                rows = self.engine.db.query("SELECT message FROM execution_logs WHERE execution_id IN (SELECT id FROM executions WHERE project_id=?) ORDER BY ts DESC LIMIT 20", (pid,))
                bot.send_message(chat_id, "\n".join(r["message"] for r in rows) or "No logs.")
        elif cmd == "/report":
            pid = arg or self._current.get(chat_id, "")
            if not pid:
                bot.send_message(chat_id, "Usage: /report <project_id>")
            else:
                st = self.engine.status(pid)
                done = sum(1 for t in st["tasks"] if t["status"] == "COMPLETED")
                bot.send_message(chat_id, f"📊 Report {pid}: {done}/{len(st['tasks'])} tasks done. Lifecycle: {st['lifecycle']}")
        elif cmd == "/projects":
            rows = self.engine.db.list_projects()
            bot.send_message(chat_id, "\n".join(f"{r['id']} — {r['name']} [{r['status']}]" for r in rows) or "No projects.")
        elif cmd == "/recover":
            pid = arg or self._current.get(chat_id, "")
            if pid:
                n = self.engine.recover(pid)
                bot.send_message(chat_id, f"♻️ Recovered {n} interrupted tasks for {pid}.")
        elif cmd == "/settings":
            s = self.settings
            bot.send_message(chat_id, f"Workers: {s.max_concurrent_workers}\nTimeout: {s.task_timeout_seconds}s\nProviders: {','.join(s.ai_provider_order)}\nDB: {s.db_path}")
        else:
            # Treat as requirements text for current project
            pid = self._current.get(chat_id)
            if pid:
                proj = self.engine.db.get_project(pid)
                if proj:
                    from execution.file_manager import FileManager
                    fm = FileManager(proj["workspace"])
                    existing = fm.read("requirements.txt") if fm.exists("requirements.txt") else ""
                    fm.write("requirements.txt", existing + "\n" + text)
                    bot.send_message(chat_id, "📝 Requirements appended. Use /new to start a fresh pipeline or /status to check.")
            else:
                bot.send_message(chat_id, "Use /new <name> to start a project.")

    def _handle_file(self, bot: TelegramBot, chat_id: int, doc: dict) -> None:
        fname = doc.get("file_name", "upload.bin")
        fsize = doc.get("file_size", 0)
        ok, msg = validate_upload(fname, fsize, self.settings.max_upload_mb)
        if not ok:
            bot.send_message(chat_id, f"⛔ {msg}")
            return
        pid = self._current.get(chat_id)
        if not pid:
            pid = self.engine.create_project("upload", requirements="Uploaded file ingest", quick=False)
            self._current[chat_id] = pid
        proj = self.engine.db.get_project(pid)
        ws = proj["workspace"] if proj else "."
        dest = str(Path(ws) / "uploads" / Path(fname).name)
        ok2, msg2 = bot.download_file(doc["file_id"], dest)
        if not ok2:
            bot.send_message(chat_id, f"⛔ Download failed: {msg2}")
            return
        if fname.lower().endswith(".zip"):
            ok3, msg3, files = safe_extract_zip(dest, str(Path(ws) / "extracted"), self.settings.max_zip_files, self.settings.max_zip_mb)
            bot.send_message(chat_id, f"📦 ZIP: {msg3} ({len(files)} files)")
        else:
            bot.send_message(chat_id, f"📎 Received {fname} ({fsize} bytes) → {dest}")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="Run local demo project")
    ap.add_argument("--status", action="store_true", help="Print DB status")
    ap.add_argument("--recover", action="store_true", help="Recover interrupted tasks")
    args = ap.parse_args()

    settings = get_settings()
    engine = OrchestratorEngine(settings)
    engine.set_progress_callback(lambda pid, msg: print(f"[{pid}] {msg}", flush=True))

    if args.status:
        for p in engine.db.list_projects():
            print(p["id"], p["name"], p["status"])
        return 0
    if args.recover:
        n = engine.db.recover_interrupted()
        print(f"Recovered {n} interrupted tasks.")
        return 0
    if args.demo:
        pid = engine.create_project("demo", requirements="Build a todo app with login, SQLite storage, and REST API. Must be fast on Android.", quick=True)
        print(f"Demo project: {pid}")
        print("Watching for completion (Ctrl+C to stop)...")
        try:
            while True:
                st = engine.status(pid)
                if st["lifecycle"] in ("COMPLETED", "FAILED"):
                    print("Final:", st["lifecycle"])
                    break
                import time; time.sleep(2)
        except KeyboardInterrupt:
            pass
        engine.shutdown()
        return 0

    errs = settings.validate()
    for e in errs:
        log.warning(e)
    if not settings.telegram_bot_token:
        log.error("TELEGRAM_BOT_TOKEN missing. Set .env or use --demo.")
        return 1
    handler = TelegramHandler(engine, settings)
    bot = TelegramBot(settings.telegram_bot_token, settings.telegram_allowed_user_id, handler, settings.max_upload_mb)
    try:
        bot.run_forever()
    except KeyboardInterrupt:
        log.info("Shutting down...")
        engine.shutdown()
    return 0

if __name__ == "__main__":
    sys.exit(main())
