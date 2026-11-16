"""Telegram command surface — authorization, intents, and replies."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from agents.registry import AGENT_NAMES
from integrations.telegram.bot import TelegramBot, is_authorized, temp_upload_path
from security.secret_manager import redact

log = logging.getLogger("telegram.handler")

HELP = """*Phone AI Agent Team* — commands
/new <name> — create a project and plan the pipeline
/status [id] — project state and task list
/agents — the 16 agents
/pause /resume — control the worker pool
/stop <id> — cancel a project
/approve <id> /reject <id> — decide a pending approval
/pending — list pending approvals
/logs [id] — recent execution logs
/report [id] — completion report
/projects — all projects
/recover <id> — requeue interrupted/failed tasks
/settings — runtime configuration (no secrets)
/health — database + provider status
Send TXT/MD/PDF/DOCX/JSON/ZIP to attach a blueprint."""

MAX_TG = 3900


class TelegramHandler:
    def __init__(self, engine, settings):
        self.engine = engine
        self.settings = settings
        self.current: dict[int, str] = {}   # chat_id → active project id

    # ---------- entry point ----------
    def __call__(self, bot: TelegramBot, update: dict) -> None:
        if not is_authorized(update, self.settings.telegram_allowed_user_id):
            msg = update.get("message") or {}
            chat = (msg.get("chat") or {}).get("id")
            if chat:
                bot.send_message(chat, "⛔ Not authorized.")
                log.warning("rejected unauthorized update from user %s",
                            (msg.get("from") or {}).get("id"))
            return
        msg = update.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        if chat_id is None:
            return
        if msg.get("document"):
            return self._handle_document(bot, chat_id, msg["document"])
        text = (msg.get("text") or "").strip()
        if not text:
            bot.send_message(chat_id, "Send text, a file, or /help.")
            return
        parts = text.split(None, 1)
        command = parts[0].split("@")[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        handler = self._commands().get(command)
        if handler is None:
            return self._append_requirements(bot, chat_id, text)
        try:
            handler(bot, chat_id, arg)
        except Exception as e:
            log.exception("command %s failed", command)
            bot.send_message(chat_id, f"⚠️ {command} failed: {redact(str(e))[:300]}")

    # ---------- commands ----------
    def _commands(self) -> dict:
        return {
            "/start": self._cmd_start, "/help": self._cmd_help,
            "/new": self._cmd_new, "/status": self._cmd_status,
            "/agents": self._cmd_agents, "/pause": self._cmd_pause,
            "/resume": self._cmd_resume, "/stop": self._cmd_stop,
            "/approve": self._cmd_approve, "/reject": self._cmd_reject,
            "/pending": self._cmd_pending, "/logs": self._cmd_logs,
            "/report": self._cmd_report, "/projects": self._cmd_projects,
            "/recover": self._cmd_recover, "/settings": self._cmd_settings,
            "/health": self._cmd_health, "/cancel": self._cmd_stop,
        }

    def _project_id(self, chat_id: int, arg: str) -> str:
        return (arg or "").split()[0] if arg else self.current.get(chat_id, "")

    def _cmd_start(self, bot, chat_id, arg):
        bot.send_message(chat_id, "👋 Ready.\n\n" + HELP)

    def _cmd_help(self, bot, chat_id, arg):
        bot.send_message(chat_id, HELP)

    def _cmd_new(self, bot, chat_id, arg):
        name = re.sub(r"[^A-Za-z0-9 _-]", "", arg or "")[:40] or "project"
        pid = self.engine.create_project(name, requirements=f"Project: {name}", quick=False)
        self.current[chat_id] = pid
        bot.send_message(chat_id, f"✅ Project `{pid}` created and planned.\n"
                                  f"Send requirements or attach a file to add detail.\n"
                                  f"Track it with /status {pid}")

    def _cmd_status(self, bot, chat_id, arg):
        pid = self._project_id(chat_id, arg)
        if not pid:
            return bot.send_message(chat_id, "Usage: /status <project_id>")
        st = self.engine.status(pid)
        if not st["project"]:
            return bot.send_message(chat_id, f"Unknown project: {pid}")
        lines = [f"*{st['project']['name']}* — {st['lifecycle']}", ""]
        for t in st["tasks"]:
            icon = {"COMPLETED": "✅", "FAILED": "❌", "EXECUTING": "▶️",
                    "QUEUED": "⏳", "CANCELLED": "🚫"}.get(t["status"], "•")
            extra = f" (retry {t['retries']})" if t["retries"] else ""
            lines.append(f"{icon} {t['agent']}{extra}")
        if st["pending_approvals"]:
            lines.append(f"\n⚠️ {len(st['pending_approvals'])} approval(s) pending — /pending")
        bot.send_message(chat_id, "\n".join(lines))

    def _cmd_agents(self, bot, chat_id, arg):
        body = "\n".join(f"{i}. {name}" for i, name in enumerate(AGENT_NAMES.values(), 1))
        bot.send_message(chat_id, "*16 agents*\n" + body)

    def _cmd_pause(self, bot, chat_id, arg):
        self.engine.pause()
        bot.send_message(chat_id, "⏸ Workers paused after the current task.")

    def _cmd_resume(self, bot, chat_id, arg):
        self.engine.resume()
        bot.send_message(chat_id, "▶️ Workers resumed.")

    def _cmd_stop(self, bot, chat_id, arg):
        pid = self._project_id(chat_id, arg)
        if not pid:
            return bot.send_message(chat_id, "Usage: /stop <project_id>")
        self.engine.cancel(pid)
        bot.send_message(chat_id, f"🛑 Cancelled {pid}.")

    def _cmd_approve(self, bot, chat_id, arg):
        if not arg:
            return bot.send_message(chat_id, "Usage: /approve <approval_id>")
        ok = self.engine.approvals.approve(arg, by=str(chat_id))
        bot.send_message(chat_id, f"✅ Approved {arg}." if ok else f"No pending approval {arg}.")

    def _cmd_reject(self, bot, chat_id, arg):
        if not arg:
            return bot.send_message(chat_id, "Usage: /reject <approval_id>")
        ok = self.engine.approvals.reject(arg, by=str(chat_id))
        bot.send_message(chat_id, f"❌ Rejected {arg}." if ok else f"No pending approval {arg}.")

    def _cmd_pending(self, bot, chat_id, arg):
        rows = self.engine.approvals.pending(self._project_id(chat_id, arg) or None)
        if not rows:
            return bot.send_message(chat_id, "No pending approvals.")
        body = "\n".join(f"`{r['id']}` — {r['action']}: {r['detail'][:80]}" for r in rows)
        bot.send_message(chat_id, body)

    def _cmd_logs(self, bot, chat_id, arg):
        pid = self._project_id(chat_id, arg)
        query = ("SELECT l.message, l.level FROM execution_logs l "
                 "JOIN executions e ON e.id = l.execution_id "
                 "WHERE e.project_id = ? ORDER BY l.id DESC LIMIT 20") if pid else \
                ("SELECT message, level FROM execution_logs ORDER BY id DESC LIMIT 20", )
        rows = self.engine.db.query(query, (pid,) if pid else ())
        if not rows:
            return bot.send_message(chat_id, "No logs yet.")
        body = "\n".join(f"[{r['level'][:4]}] {r['message'][:160]}" for r in reversed(rows))
        bot.send_message(chat_id, f"*Logs*\n{body}")

    def _cmd_report(self, bot, chat_id, arg):
        pid = self._project_id(chat_id, arg)
        if not pid:
            return bot.send_message(chat_id, "Usage: /report <project_id>")
        bot.send_message(chat_id, self.engine.report(pid))

    def _cmd_projects(self, bot, chat_id, arg):
        rows = self.engine.db.list_projects()[:15]
        if not rows:
            return bot.send_message(chat_id, "No projects yet — use /new.")
        body = "\n".join(f"`{r['id']}` {r['name']} — {r['lifecycle']}" for r in rows)
        bot.send_message(chat_id, body)

    def _cmd_recover(self, bot, chat_id, arg):
        pid = self._project_id(chat_id, arg)
        if not pid:
            return bot.send_message(chat_id, "Usage: /recover <project_id>")
        res = self.engine.recover(pid)
        if not res.get("ok"):
            return bot.send_message(chat_id, f"⚠️ {res.get('error')}")
        bot.send_message(chat_id, f"♻️ Recovered {pid}: {res['interrupted']} interrupted, "
                                  f"{res['requeued']} failed task(s) requeued.")

    def _cmd_settings(self, bot, chat_id, arg):
        s = self.settings
        h = self.engine.router.health()
        providers = ", ".join(f"{k}={v['status']}" for k, v in h["providers"].items())
        bot.send_message(chat_id, "\n".join([
            f"Workers: {s.max_concurrent_workers}",
            f"Task timeout: {s.task_timeout_seconds}s",
            f"Upload limit: {s.max_upload_mb}MB",
            f"Providers: {providers}",
            f"AI mode: {h['mode']}",
            f"Fallback: {h['fallback_enabled']}",
            f"DB: {s.db_path}",
        ]))

    def _cmd_health(self, bot, chat_id, arg):
        h = self.engine.router.health()
        try:
            counts = self.engine.db.query(
                "SELECT status, COUNT(*) n FROM tasks GROUP BY status")
        except Exception:
            counts = []
        body = [f"AI mode: *{h['mode']}*"]
        body += [f"{k}: {v['status']} ({v['model']})" for k, v in h["providers"].items()]
        if counts:
            body.append("Tasks: " + ", ".join(f"{c['status']}={c['n']}" for c in counts))
        bot.send_message(chat_id, "\n".join(body))

    # ---------- free text & files ----------
    def _append_requirements(self, bot, chat_id, text: str) -> None:
        pid = self.current.get(chat_id)
        if not pid:
            return bot.send_message(chat_id, "Use /new <name> first, then send requirements.")
        self.engine._append_requirements(pid, text)
        bot.send_message(chat_id, "📝 Requirements appended to the project.")

    def _handle_document(self, bot: TelegramBot, chat_id: int, doc: dict) -> None:
        name = Path(doc.get("file_name") or "upload.bin").name
        pid = self.current.get(chat_id)
        if not pid:
            return bot.send_message(chat_id, "Use /new <name> first, then attach the file.")
        suffix = Path(name).suffix
        tmp = temp_upload_path(suffix)
        ok, msg = bot.download_file(doc["file_id"], tmp)
        if not ok:
            return bot.send_message(chat_id, f"⛔ Download refused: {msg}")
        try:
            ok, msg = self.engine.ingest_upload(pid, tmp, name)
        finally:
            try:
                Path(tmp).unlink()
            except OSError:
                pass
        icon = "📦" if name.lower().endswith(".zip") else "📎"
        bot.send_message(chat_id, f"{icon} {name}: {msg}" if ok else f"⛔ {name}: {msg}")
