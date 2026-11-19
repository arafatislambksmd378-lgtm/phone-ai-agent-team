"""Telegram command & callback surface — auth, inline approvals, progress, and artifact delivery."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from integrations.telegram.bot import TelegramBot, extract_user_id, is_authorized, temp_upload_path
from security.secret_manager import redact

log = logging.getLogger("telegram.handler")

HELP = """*Phone AI Agent Team* — commands
/new <name> [blueprint] — create a project (starts immediately if blueprint included, else waits for text/file)
/build [id] — start or replan dynamic pipeline for active project
/status [id] — project lifecycle, category & task statuses
/tasks [id] — list all tasks (from Telegram & Android dashboard)
/task <task_id> [cancel|retry|resume] — control one task (shared with Android)
/sync [id] — recent dashboard ↔ Telegram synchronization events
/agents — list all 16 specialist agents & current state
/pause /resume — pause or resume the worker pool
/stop <id> — cancel a project
/approve <id> /reject <id> — decide a pending approval
/pending — list pending approvals with inline buttons
/logs [id] — recent redacted execution logs
/report [id] — completion & AI usage report
/artifact [id] — download packaged project ZIP archive
/projects — list recent projects
/recover <id> — requeue interrupted or failed tasks
/settings — runtime configuration (no secrets)
/health — AI router, database & Android resource status
Send TXT/MD/PDF/DOCX/JSON/CSV/ZIP to attach a blueprint."""

MAX_TG = 3900


class TelegramHandler:
    def __init__(self, engine, settings):
        self.engine = engine
        self.settings = settings
        self.current: dict[int, str] = {}   # chat_id -> active project id
        self._project_chats: dict[str, int] = {}
        self._bot: TelegramBot | None = None
        self._default_chat_id: int = int(getattr(settings, "telegram_allowed_user_id", 0) or 0)
        self._seen_callback_ids: set[str] = set()

    def bind_bot(self, bot: TelegramBot, default_chat_id: int | None = None) -> None:
        """Connect OrchestratorEngine progress, approval, and completion events to Telegram."""
        self._bot = bot
        if default_chat_id:
            self._default_chat_id = int(default_chat_id)
        self.engine.set_progress_callback(self._on_engine_progress)
        self.engine.set_approval_callback(self._on_engine_approval)
        self.engine.set_completion_callback(self._on_engine_completion)

    def _chat_for_project(self, project_id: str) -> int:
        return self._project_chats.get(project_id) or self._default_chat_id

    def _on_engine_progress(self, project_id: str, message: str) -> None:
        if self._bot is None:
            return
        chat_id = self._chat_for_project(project_id)
        if not chat_id:
            return
        # Deliver key milestones, dashboard-originated activity, and errors without flooding
        if any(message.startswith(p) for p in (
            "Planned ", "✗ ", "↻ ", "⚠️", "⏸", "♻️", "🔄",
            "📁 Project created (dashboard)", "📝 Task created (dashboard)",
            "📦 Uploaded ZIP (dashboard)", "📎 Uploaded ", "🔐 ",
        )):
            self._bot.send_message(chat_id, f"[{project_id}] {message}")

    def _on_engine_approval(self, approval_row: dict) -> None:
        if self._bot is None:
            return
        pid = approval_row.get("project_id", "")
        chat_id = self._chat_for_project(pid)
        if chat_id:
            self._bot.send_approval_prompt(chat_id, approval_row)

    def _on_engine_completion(self, project_id: str, report_text: str, zip_path: str) -> None:
        if self._bot is None:
            return
        chat_id = self._chat_for_project(project_id)
        if not chat_id:
            return
        self._bot.send_message(chat_id, f"🎉 Build complete!\n\n{report_text}")
        if zip_path and Path(zip_path).is_file():
            self._bot.send_document(chat_id, zip_path, caption=f"Deliverable archive for {project_id}")

    # ---------- main update router ----------
    def __call__(self, bot: TelegramBot, update: dict) -> None:
        if not is_authorized(update, self.settings.telegram_allowed_user_id):
            uid = extract_user_id(update)
            if "callback_query" in update:
                cb = update.get("callback_query") or {}
                data = str(cb.get("data") or "")
                aid = data.split(":", 1)[1] if ":" in data else ""
                self.engine.approvals.record_unauthorized(aid, actor=str(uid or "unknown"), detail=data)
                if cb.get("id"):
                    bot.answer_callback_query(cb["id"], "⛔ Not authorized.", show_alert=True)
                log.warning("rejected unauthorized callback_query (identity withheld)")
                return
            msg = update.get("message") or update.get("edited_message") or {}
            chat = (msg.get("chat") or {}).get("id")
            if chat:
                bot.send_message(chat, "⛔ Not authorized.")
                log.warning("rejected unauthorized update (identity withheld)")
            return

        if "callback_query" in update:
            return self._handle_callback_query(bot, update["callback_query"])

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

    # ---------- inline approval callbacks ----------
    def _handle_callback_query(self, bot: TelegramBot, cb: dict) -> None:
        cb_id = str(cb.get("id") or "")
        data = str(cb.get("data") or "").strip()
        user_id = str((cb.get("from") or {}).get("id") or "")
        msg = cb.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        message_id = msg.get("message_id")

        # Duplicate callback_query_id protection
        if cb_id and cb_id in self._seen_callback_ids:
            bot.answer_callback_query(cb_id, "Duplicate callback ignored.")
            return
        if cb_id:
            if len(self._seen_callback_ids) > 2000:
                self._seen_callback_ids.clear()
            self._seen_callback_ids.add(cb_id)

        if ":" not in data:
            if cb_id:
                bot.answer_callback_query(cb_id, "Unsupported action.")
            return
        verb, aid = data.split(":", 1)
        verb = verb.lower().strip()
        aid = aid.strip()

        if verb not in ("approve", "reject") or not aid:
            if cb_id:
                bot.answer_callback_query(cb_id, "Invalid callback payload.")
            return

        approved = (verb == "approve")
        ok = (
            self.engine.approvals.approve(aid, by=user_id)
            if approved
            else self.engine.approvals.reject(aid, by=user_id)
        )
        if not ok:
            status_now = self.engine.approvals.status(aid) or "UNKNOWN"
            if cb_id:
                bot.answer_callback_query(
                    cb_id, f"Cannot {verb}: approval is {status_now}.", show_alert=True
                )
            if chat_id:
                bot.send_message(chat_id, f"⚠️ Approval `{aid}` could not be changed (status: {status_now}).")
            return

        self.engine.on_approval_decided(aid, approved=approved, source="telegram")
        if cb_id:
            bot.answer_callback_query(cb_id, f"{'Approved' if approved else 'Rejected'} {aid}")
        if chat_id and message_id:
            bot.clear_inline_buttons(chat_id, message_id)
        if chat_id:
            icon = "✅" if approved else "❌"
            bot.send_message(chat_id, f"{icon} {'Approved' if approved else 'Rejected'} `{aid}`.")

    # ---------- commands ----------
    def _commands(self) -> dict:
        return {
            "/start": self._cmd_start, "/help": self._cmd_help,
            "/new": self._cmd_new, "/build": self._cmd_build,
            "/status": self._cmd_status, "/tasks": self._cmd_tasks,
            "/task": self._cmd_task,
            "/sync": self._cmd_sync, "/agents": self._cmd_agents,
            "/pause": self._cmd_pause, "/resume": self._cmd_resume,
            "/stop": self._cmd_stop, "/cancel": self._cmd_stop,
            "/approve": self._cmd_approve, "/reject": self._cmd_reject,
            "/pending": self._cmd_pending, "/logs": self._cmd_logs,
            "/report": self._cmd_report, "/artifact": self._cmd_artifact,
            "/download": self._cmd_artifact, "/projects": self._cmd_projects,
            "/recover": self._cmd_recover, "/settings": self._cmd_settings,
            "/health": self._cmd_health,
        }

    def _project_id(self, chat_id: int, arg: str) -> str:
        return (arg or "").split()[0] if arg else self.current.get(chat_id, "")

    def _cmd_start(self, bot, chat_id, arg):
        bot.send_message(chat_id, "👋 Ready.\n\n" + HELP)

    def _cmd_help(self, bot, chat_id, arg):
        bot.send_message(chat_id, HELP)

    def _cmd_new(self, bot, chat_id, arg):
        raw = (arg or "").strip()
        # Split `/new <name> <optional blueprint...>` or `/new <name>: <blueprint>`
        if ":" in raw:
            head, tail = raw.split(":", 1)
            name_part, blueprint = head.strip(), tail.strip()
        else:
            tokens = raw.split(None, 1)
            name_part = tokens[0] if tokens else "project"
            blueprint = tokens[1].strip() if len(tokens) > 1 else ""

        name = re.sub(r"[^A-Za-z0-9 _-]", "", name_part)[:40] or "project"
        has_inline_blueprint = len(blueprint) >= 8
        pid = self.engine.create_project(
            name,
            requirements=blueprint if has_inline_blueprint else f"Project: {name}",
            quick=False,
            auto_start=has_inline_blueprint,
            source="telegram",
        )
        self.current[chat_id] = pid
        self._project_chats[pid] = chat_id

        if has_inline_blueprint:
            st = self.engine.status(pid)
            stype = (st.get("blueprint_analysis") or {}).get("software_type", "web_app")
            bot.send_message(
                chat_id,
                f"✅ Project `{pid}` ({name}) created and dynamically planned as `{stype}`.\n"
                f"Planned {len(st['tasks'])} tasks. Track with /status {pid}",
            )
        else:
            bot.send_message(
                chat_id,
                f"✅ Project `{pid}` ({name}) initialized.\n"
                f"Send your blueprint requirements as text or attach a file (TXT/MD/PDF/DOCX/JSON/CSV/ZIP) "
                f"to dynamically plan and start the build, or run /build {pid}.",
            )

    def _cmd_build(self, bot, chat_id, arg):
        pid = self._project_id(chat_id, arg)
        if not pid:
            return bot.send_message(chat_id, "Usage: /build <project_id>")
        res = self.engine.replan_project(pid, source="telegram")
        if not res.get("ok"):
            return bot.send_message(chat_id, f"⚠️ {res.get('error')}")
        st = self.engine.status(pid)
        stype = (st.get("blueprint_analysis") or {}).get("software_type", "web_app")
        bot.send_message(
            chat_id,
            f"🚀 Project `{pid}` planned (`{stype}`, {len(st['tasks'])} tasks) and workers started.",
        )

    def _cmd_status(self, bot, chat_id, arg):
        pid = self._project_id(chat_id, arg)
        if not pid:
            return bot.send_message(chat_id, "Usage: /status <project_id>")
        st = self.engine.status(pid)
        if not st["project"]:
            return bot.send_message(chat_id, f"Unknown project: {pid}")
        analysis = st.get("blueprint_analysis") or {}
        stype = analysis.get("software_type")
        header = f"*{st['project']['name']}* (`{pid}`) — {st['lifecycle']}"
        if stype:
            header += f" [{stype}]"
        lines = [header, ""]
        if not st["tasks"]:
            lines.append("⏳ Waiting for blueprint text or file upload (or /build).")
        for t in st["tasks"]:
            icon = {
                "COMPLETED": "✅", "FAILED": "❌", "EXECUTING": "▶️",
                "QUEUED": "⏳", "CANCELLED": "🚫", "SKIPPED": "⏭️",
            }.get(t["status"], "•")
            extra = f" (retry {t['retries']})" if t["retries"] else ""
            lines.append(f"{icon} {t['agent']}{extra}")
        if st["pending_approvals"]:
            lines.append(f"\n⚠️ {len(st['pending_approvals'])} approval(s) pending — /pending")
        bot.send_message(chat_id, "\n".join(lines))

    def _cmd_tasks(self, bot, chat_id, arg):
        import json as _json
        pid = self._project_id(chat_id, arg)
        if not pid:
            # Default to most recent project so dashboard-created projects are immediately visible
            projs = self.engine.db.list_projects()
            if not projs:
                return bot.send_message(chat_id, "No projects yet — use /new or create one in Android dashboard.")
            pid = projs[0]["id"]
        proj = self.engine.db.get_project(pid)
        if not proj:
            return bot.send_message(chat_id, f"Unknown project: {pid}")
        tasks = self.engine.db.list_tasks(pid)
        if not tasks:
            return bot.send_message(chat_id, f"*{proj['name']}* (`{pid}`): no tasks yet.")
        lines = [f"*Tasks — {proj['name']}* (`{pid}`)"]
        for t in tasks[:30]:
            try:
                src = _json.loads(t.get("payload") or "{}").get("source") or "orchestrator"
            except Exception:
                src = "orchestrator"
            lines.append(f"• `{t['id']}` [{t['status']}] `{t['agent_id']}` — {t['title']} _({src})_")
        bot.send_message(chat_id, "\n".join(lines))

    def _cmd_task(self, bot, chat_id, arg):
        parts = (arg or "").split()
        if not parts:
            return bot.send_message(chat_id, "Usage: /task <task_id> [cancel|retry|resume]")
        tid = parts[0]
        task = self.engine.db.get_task(tid)
        if not task:
            return bot.send_message(chat_id, f"Unknown task: {tid}")
        if len(parts) == 1:
            return bot.send_message(
                chat_id,
                f"`{tid}` [{task['status']}] `{task['agent_id']}` — {task['title']}\n"
                f"retries={task.get('retries', 0)}/{task.get('max_retries', 3)} "
                f"cancel_requested={bool(task.get('cancel_requested', 0))}",
            )
        action = parts[1].lower()
        res = self.engine.control_task(task["project_id"], tid, action, actor="telegram")
        if not res.get("ok"):
            return bot.send_message(chat_id, f"⚠️ {res.get('error', 'control failed')}")
        bot.send_message(
            chat_id,
            f"🎛 `{tid}` {action}: {res.get('status')}. Android sees the same task state.",
        )

    def _cmd_sync(self, bot, chat_id, arg):
        pid = (arg or "").split()[0] if arg else ""
        events = self.engine.db.list_sync_events(pid or None, limit=15)
        if not events:
            return bot.send_message(chat_id, "No synchronization events recorded yet.")
        lines = ["*Synchronization Feed*"]
        for ev in events:
            lines.append(f"• [{ev['source']}] `{ev['event']}` {ev['project_id']}: {ev['detail'][:100]}")
        bot.send_message(chat_id, "\n".join(lines))

    def _cmd_agents(self, bot, chat_id, arg):
        rows = self.engine.agent_statuses()
        body = "\n".join(
            f"{i:02d}. {r['name']} (`{r['id']}`) — {r['status']}"
            for i, r in enumerate(rows, 1)
        )
        bot.send_message(chat_id, "*16 Specialist Agents*\n" + body)

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
        aid = arg.split()[0]
        ok = self.engine.approvals.approve(aid, by=str(chat_id))
        if ok:
            self.engine.on_approval_decided(aid, approved=True, source="telegram")
            bot.send_message(chat_id, f"✅ Approved {aid}.")
        else:
            bot.send_message(chat_id, f"No pending approval {aid}.")

    def _cmd_reject(self, bot, chat_id, arg):
        if not arg:
            return bot.send_message(chat_id, "Usage: /reject <approval_id>")
        aid = arg.split()[0]
        ok = self.engine.approvals.reject(aid, by=str(chat_id))
        if ok:
            self.engine.on_approval_decided(aid, approved=False, source="telegram")
            bot.send_message(chat_id, f"❌ Rejected {aid}.")
        else:
            bot.send_message(chat_id, f"No pending approval {aid}.")

    def _cmd_pending(self, bot, chat_id, arg):
        rows = self.engine.approvals.pending(self._project_id(chat_id, arg) or None)
        if not rows:
            return bot.send_message(chat_id, "No pending approvals.")
        for r in rows[:5]:
            bot.send_approval_prompt(chat_id, r)

    def _cmd_logs(self, bot, chat_id, arg):
        pid = self._project_id(chat_id, arg)
        if pid:
            sql = (
                "SELECT l.message, l.level FROM execution_logs l "
                "JOIN executions e ON e.id = l.execution_id "
                "WHERE e.project_id = ? ORDER BY l.id DESC LIMIT 20"
            )
            params = (pid,)
        else:
            sql = "SELECT message, level FROM execution_logs ORDER BY id DESC LIMIT 20"
            params = ()
        rows = self.engine.db.query(sql, params)
        if not rows:
            return bot.send_message(chat_id, "No logs yet.")
        body = "\n".join(f"[{r['level'][:4]}] {r['message'][:160]}" for r in reversed(rows))
        bot.send_message(chat_id, f"*Logs*\n{body}")

    def _cmd_report(self, bot, chat_id, arg):
        pid = self._project_id(chat_id, arg)
        if not pid:
            return bot.send_message(chat_id, "Usage: /report <project_id>")
        bot.send_message(chat_id, self.engine.report(pid))

    def _cmd_artifact(self, bot: TelegramBot, chat_id: int, arg: str):
        pid = self._project_id(chat_id, arg)
        if not pid:
            return bot.send_message(chat_id, "Usage: /artifact <project_id>")
        zip_path = self.engine.deliverable_zip_path(pid)
        if not zip_path or not Path(zip_path).is_file():
            return bot.send_message(chat_id, f"No deliverable archive available for {pid}.")
        res = bot.send_document(chat_id, zip_path, caption=f"Deliverable archive for {pid}")
        if not res.get("ok"):
            bot.send_message(chat_id, f"⚠️ Could not upload archive: {res.get('error', 'unknown')}")

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
        bot.send_message(
            chat_id,
            f"♻️ Recovered {pid}: {res['interrupted']} interrupted, "
            f"{res['requeued']} failed task(s) requeued.",
        )

    def _cmd_settings(self, bot, chat_id, arg):
        s = self.settings
        h = self.engine.router.health()
        providers = ", ".join(f"{k}={v['status']}" for k, v in h["providers"].items())
        eff_workers = self.engine.resources.effective_worker_limit(s.max_concurrent_workers)
        bot.send_message(chat_id, "\n".join([
            f"Workers: {eff_workers} (configured max={s.max_concurrent_workers})",
            f"Task timeout: {s.task_timeout_seconds}s",
            f"Approval TTL: {s.approval_ttl_seconds}s",
            f"Upload limit: {s.max_upload_mb}MB",
            f"Providers: {providers}",
            f"AI mode: {h['mode']}",
            f"Fallback: {h['fallback_enabled']}",
            f"DB: {s.db_path}",
        ]))

    def _cmd_health(self, bot, chat_id, arg):
        h = self.engine.router.health()
        snap = self.engine.resources.snapshot()
        try:
            counts = self.engine.db.query("SELECT status, COUNT(*) n FROM tasks GROUP BY status")
        except Exception:
            counts = []
        body = [f"AI mode: *{h['mode']}*"]
        body += [f"{k}: {v['status']} ({v['model']})" for k, v in h["providers"].items()]
        mem = snap["memory"]
        disk = snap["storage"]
        body.append(
            f"RAM avail: {mem['available_mb']}MB | Process RSS: {mem['process_rss_mb']}MB | "
            f"Disk free: {disk['free_mb']}MB"
        )
        if counts:
            body.append("Tasks: " + ", ".join(f"{c['status']}={c['n']}" for c in counts))
        bot.send_message(chat_id, "\n".join(body))

    # ---------- free text & blueprint uploads ----------
    def _append_requirements(self, bot, chat_id, text: str) -> None:
        pid = self.current.get(chat_id)
        if not pid:
            return bot.send_message(chat_id, "Use /new <name> first, then send requirements.")
        self._project_chats[pid] = chat_id
        was_waiting = (self.engine.lifecycle.get(pid) == "INPUT_RECEIVED"
                       and not self.engine.db.list_tasks(pid))
        res = self.engine.replan_project(pid, additional_requirements=text, source="telegram")
        if was_waiting and res.get("ok"):
            st = self.engine.status(pid)
            stype = (st.get("blueprint_analysis") or {}).get("software_type", "web_app")
            bot.send_message(
                chat_id,
                f"📝 Blueprint saved and dynamically planned as `{stype}` "
                f"({len(st['tasks'])} tasks). Workers started!",
            )
        else:
            bot.send_message(chat_id, "📝 Requirements appended and pipeline updated.")

    def _handle_document(self, bot: TelegramBot, chat_id: int, doc: dict) -> None:
        name = Path(doc.get("file_name") or "upload.bin").name
        pid = self.current.get(chat_id)
        if not pid:
            return bot.send_message(chat_id, "Use /new <name> first, then attach the file.")
        self._project_chats[pid] = chat_id
        suffix = Path(name).suffix
        tmp = temp_upload_path(suffix)
        ok, msg = bot.download_file(doc["file_id"], tmp)
        if not ok:
            return bot.send_message(chat_id, f"⛔ Download refused: {msg}")
        try:
            ok, msg = self.engine.ingest_upload(pid, tmp, name, auto_plan_if_waiting=True, source="telegram")
        finally:
            try:
                Path(tmp).unlink()
            except OSError:
                pass
        icon = "📦" if name.lower().endswith(".zip") else "📎"
        bot.send_message(chat_id, f"{icon} {name}: {msg}" if ok else f"⛔ {name}: {msg}")
