"""Phone AI Agent Team — entry point.

  python3 main.py                # Telegram bot + on-demand worker pool
  python3 main.py --demo         # run a full local pipeline, no Telegram needed
  python3 main.py --status       # list projects from the database
  python3 main.py --health       # provider + database status, then exit
  python3 main.py --recover      # requeue tasks interrupted by a crash, then exit
  python3 main.py --selfcheck    # verify install (deps, dirs, DB, providers)
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from logging.handlers import RotatingFileHandler

from config.settings import get_settings
from integrations.telegram.handler import TelegramHandler
from orchestrator.engine import OrchestratorEngine

log = logging.getLogger("main")


def setup_logging(settings) -> None:
    level = getattr(logging, str(settings.log_level).upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)
    try:  # rotating file log — small enough for a phone
        settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(settings.log_file, maxBytes=1_000_000, backupCount=2)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as e:
        log.warning("file logging disabled: %s", e)


def cmd_status(engine) -> int:
    projects = engine.db.list_projects()
    if not projects:
        print("No projects.")
        return 0
    for p in projects:
        tasks = engine.db.list_tasks(p["id"])
        done = sum(1 for t in tasks if t["status"] == "COMPLETED")
        print(f"{p['id']}  {p['lifecycle']:<20} {done}/{len(tasks)} tasks  {p['name']}")
    return 0


def cmd_health(engine, settings) -> int:
    h = engine.router.health()
    print(f"AI mode        : {h['mode']}")
    print(f"Fallback       : {h['fallback_enabled']}")
    for name, info in h["providers"].items():
        print(f"  {name:<10} {info['status']:<15} model={info['model']}")
    print(f"Database       : {settings.db_path} ({'ok' if settings.db_path.exists() else 'missing'})")
    print(f"Workers        : {settings.max_concurrent_workers}")
    print(f"Workspace root : {settings.workspace_root}")
    return 0


def cmd_selfcheck(engine, settings) -> int:
    problems = []
    print("== self check ==")
    print(f"python      : {sys.version.split()[0]}")
    for mod in ("sqlite3", "urllib.request", "http.server", "json", "zipfile"):
        try:
            __import__(mod)
            print(f"module {mod:<16} ok")
        except Exception as e:
            problems.append(f"module {mod} missing: {e}")
    for err in settings.validate():
        print(f"config note : {err}")
    settings.ensure_dirs()
    print(f"data dir    : {settings.db_path.parent} ok")
    print(f"projects dir: {settings.workspace_root} ok")
    try:
        engine.db.query("SELECT 1")
        print("database    : ok")
    except Exception as e:
        problems.append(f"database unusable: {e}")
    mode = engine.router.mode()
    print(f"AI mode     : {mode}" + ("" if mode == "live" else "  (mock — set an API key for real AI)"))
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(" -", p)
        return 1
    print("\nself check passed.")
    return 0


def cmd_demo(engine, settings, quick: bool = False) -> int:
    pid = engine.create_project(
        "demo",
        requirements=("Build a small task list app. Users must be able to add items, "
                      "mark them done and delete them. It must run on Android Termux with "
                      "SQLite and no external database."),
        quick=quick)
    print(f"demo project: {pid}")
    deadline = time.time() + max(120, settings.task_timeout_seconds)
    last = ""
    while time.time() < deadline:
        st = engine.status(pid)
        if st["lifecycle"] != last:
            print(f"state: {st['lifecycle']}")
            last = st["lifecycle"]
        if st["lifecycle"] in ("COMPLETED", "FAILED", "CANCELLED"):
            break
        time.sleep(0.5)
    print(engine.report(pid))
    proj = engine.db.get_project(pid)
    if proj:
        print(f"\nworkspace: {proj['workspace']}")
    return 0 if engine.status(pid)["lifecycle"] == "COMPLETED" else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Phone AI Agent Team")
    ap.add_argument("--demo", action="store_true", help="run a full pipeline locally")
    ap.add_argument("--quick", action="store_true", help="with --demo: 5-step short pipeline")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--health", action="store_true")
    ap.add_argument("--recover", action="store_true")
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args(argv)

    settings = get_settings()
    settings.ensure_dirs()
    setup_logging(settings)
    engine = OrchestratorEngine(settings)

    try:
        if args.status:
            return cmd_status(engine)
        if args.health:
            return cmd_health(engine, settings)
        if args.selfcheck:
            return cmd_selfcheck(engine, settings)
        if args.recover:
            n = engine.db.recover_interrupted()
            print(f"requeued {n} interrupted task(s)")
            return 0
        if args.demo:
            return cmd_demo(engine, settings, quick=args.quick)

        errors = settings.validate()
        for e in errors:
            log.warning(e)
        if not settings.telegram_bot_token:
            log.error("TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env, or run --demo.")
            return 1
        if not settings.telegram_allowed_user_id:
            log.error("TELEGRAM_ALLOWED_USER_ID is not set — every command would be rejected.")
            return 1

        from integrations.telegram.bot import TelegramBot
        handler = TelegramHandler(engine, settings)
        bot = TelegramBot(settings.telegram_bot_token, settings.telegram_allowed_user_id,
                          handler, settings.max_upload_mb)

        def _shutdown(signum, frame):
            log.info("signal %s — shutting down", signum)
            bot.stop()
            engine.shutdown()
            sys.exit(0)

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)
        log.info("listening for Telegram updates; workers start on demand")
        bot.run_forever()
        return 0
    finally:
        engine.shutdown()


if __name__ == "__main__":
    sys.exit(main())
