"""Phone AI Agent Team — entry point.

  python3 main.py                # Telegram bot + on-demand worker pool
  python3 main.py --web          # Web API Key & Live Verification Portal (0.0.0.0:8080)
  python3 main.py --demo         # run a local pipeline, no Telegram needed
  python3 main.py --status       # list projects from the database
  python3 main.py --health       # provider, database & Android resource status
  python3 main.py --recover      # requeue tasks interrupted by a crash, then exit
  python3 main.py --selfcheck    # verify install (deps, dirs, DB, providers, resources)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config.settings import get_settings
from integrations.telegram.handler import TelegramHandler
from orchestrator.engine import OrchestratorEngine
from security.secret_manager import redact

log = logging.getLogger("main")
PID_FILE = Path("./data/phone_agent.pid")


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(settings) -> None:
    level = getattr(logging, str(settings.log_level).upper(), logging.INFO)
    fmt = _RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)
    try:
        settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(settings.log_file, maxBytes=1_000_000, backupCount=2)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as e:
        log.warning("file logging disabled: %s", e)


def cmd_status(engine: OrchestratorEngine) -> int:
    projects = engine.db.list_projects()
    if not projects:
        print("No projects.")
        return 0
    for p in projects:
        tasks = engine.db.list_tasks(p["id"])
        done = sum(1 for t in tasks if t["status"] == "COMPLETED")
        try:
            stype = json.loads(p.get("metadata") or "{}").get("software_type", "web_app")
        except Exception:
            stype = "web_app"
        print(f"{p['id']}  {p['lifecycle']:<20} {stype:<15} {done}/{len(tasks)} tasks  {p['name']}")
    return 0


def cmd_health(engine: OrchestratorEngine, settings) -> int:
    h = engine.router.health()
    snap = engine.resources.snapshot()
    eff_workers = engine.resources.effective_worker_limit(settings.max_concurrent_workers)
    print(f"AI mode        : {h['mode']}")
    print(f"Fallback       : {h['fallback_enabled']}")
    for name, info in h["providers"].items():
        print(f"  {name:<10} {info['status']:<15} model={info['model']}")
    print(f"Database       : {settings.db_path} ({'ok' if settings.db_path.exists() else 'missing'})")
    print(f"Workers        : {eff_workers} active max (configured={settings.max_concurrent_workers})")
    print(f"Termux env     : {snap['is_termux']}")
    print(f"Memory         : avail={snap['memory']['available_mb']} MB, rss={snap['memory']['process_rss_mb']} MB")
    print(f"Storage        : free={snap['storage']['free_mb']} MB / {snap['storage']['total_mb']} MB")
    print(f"Workspace root : {settings.workspace_root}")
    return 0


def cmd_selfcheck(engine: OrchestratorEngine, settings) -> int:
    problems = []
    print("== self check ==")
    print(f"python      : {sys.version.split()[0]}")
    for mod in ("sqlite3", "urllib.request", "http.server", "json", "zipfile", "xml.etree.ElementTree"):
        try:
            __import__(mod)
            print(f"module {mod:<24} ok")
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
    res_ok, res_msg, snap = engine.resources.check_resources_for_task()
    print(f"resources   : {res_msg} (free disk={snap['storage']['free_mb']} MB)")
    if not res_ok:
        problems.append(f"resource check failed: {res_msg}")
    mode = engine.router.mode()
    print(f"AI mode     : {mode}" + ("" if mode == "live" else "  (mock — set an API key for real AI)"))
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(" -", p)
        return 1
    print("\nself check passed.")
    return 0


def cmd_demo(engine: OrchestratorEngine, settings, quick: bool = False,
             blueprint: str = "") -> int:
    req = blueprint or (
        "Build a small task list app. Users must be able to add items, "
        "mark them done and delete them. It must run on Android Termux with "
        "SQLite and no external database."
    )
    pid = engine.create_project("demo", requirements=req, quick=quick)
    print(f"demo project: {pid}")
    deadline = time.time() + max(120, settings.task_timeout_seconds)
    last = ""
    while time.time() < deadline:
        st = engine.status(pid)
        if st["lifecycle"] != last:
            print(f"state: {st['lifecycle']}")
            last = st["lifecycle"]
        if st["lifecycle"] in ("COMPLETED", "FAILED", "CANCELLED", "WAITING_FOR_APPROVAL"):
            break
        time.sleep(0.3)
    print(engine.report(pid))
    proj = engine.db.get_project(pid)
    if proj:
        print(f"\nworkspace: {proj['workspace']}")
    return 0 if engine.status(pid)["lifecycle"] == "COMPLETED" else 1


def _write_pid() -> None:
    try:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass


def _remove_pid() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Phone AI Agent Team")
    ap.add_argument("--web", action="store_true", help="launch API Key & Live Verification web page")
    ap.add_argument("--api", action="store_true", help="serve authenticated Android JSON API + Orchestrator")
    ap.add_argument("--with-api", action="store_true", help="run Telegram bot AND authenticated Android API together")
    ap.add_argument("--api-host", default=os.getenv("API_HOST", "127.0.0.1"),
                    help="with --api/--with-api: bind host (default 127.0.0.1; use behind HTTPS reverse proxy)")
    ap.add_argument("--api-port", type=int, default=int(os.getenv("API_PORT", "8000")),
                    help="with --api/--with-api: bind port (default 8000)")
    ap.add_argument("--tls-cert", default=os.getenv("API_TLS_CERT", ""), help="optional PEM TLS certificate path")
    ap.add_argument("--tls-key", default=os.getenv("API_TLS_KEY", ""), help="optional PEM TLS private key path")
    ap.add_argument("--host", default="127.0.0.1", help="with --web: bind host (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=int(os.getenv("WEB_SETUP_PORT", "8080")),
                    help="with --web: bind port (default 8080)")
    ap.add_argument("--demo", action="store_true", help="run a local pipeline without Telegram")
    ap.add_argument("--quick", action="store_true", help="with --demo: 5-step short pipeline")
    ap.add_argument("--blueprint", default="", help="with --demo: custom blueprint specification text")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--health", action="store_true")
    ap.add_argument("--recover", action="store_true")
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args(argv)

    settings = get_settings()
    settings.ensure_dirs()
    setup_logging(settings)

    if args.web:
        import threading
        from integrations.web.setup_server import create_setup_http_server
        try:
            srv = create_setup_http_server(
                host=args.host, port=args.port, auth_token=settings.web_setup_token,
            )
        except ValueError as ve:
            log.error("%s", ve)
            return 1
        log.info("API Key & Live Verification Portal listening on http://%s:%d", args.host, args.port)

        def _web_shutdown(signum, frame):
            log.info("signal %s — stopping web portal", signum)
            threading.Thread(target=srv.shutdown, daemon=True).start()

        signal.signal(signal.SIGINT, _web_shutdown)
        signal.signal(signal.SIGTERM, _web_shutdown)
        try:
            srv.serve_forever()
        finally:
            srv.controller.shutdown()  # type: ignore[attr-defined]
            srv.server_close()
        return 0

    engine = OrchestratorEngine(settings)
    api_srv = None

    try:
        if args.api:
            import threading
            from integrations.api.server import create_api_server
            from integrations.github.api import GitHubAPI
            from integrations.github.executor import GitHubExecutionService

            gh_svc = GitHubExecutionService(engine.db, GitHubAPI(settings.github_token), engine.approvals)
            try:
                api_srv = create_api_server(
                    engine.db,
                    host=args.api_host,
                    port=args.api_port,
                    api_token=settings.api_auth_token,
                    github_service=gh_svc,
                    engine=engine,
                    tls_cert=args.tls_cert,
                    tls_key=args.tls_key,
                    max_upload_mb=settings.max_upload_mb,
                )
            except ValueError as ve:
                log.error("%s", ve)
                return 1
            rec = engine.startup_recovery()
            if rec["interrupted"]:
                log.info("startup recovery requeued %d task(s)", rec["interrupted"])
            scheme = "https" if (args.tls_cert and args.tls_key) else "http"
            log.info("Authenticated Android API + Orchestrator listening on %s://%s:%d",
                     scheme, args.api_host, api_srv.server_address[1])
            _write_pid()

            def _api_shutdown(signum, frame):
                log.info("signal %s — stopping API server", signum)
                threading.Thread(target=api_srv.shutdown, daemon=True).start()

            signal.signal(signal.SIGINT, _api_shutdown)
            signal.signal(signal.SIGTERM, _api_shutdown)
            api_srv.serve_forever()
            return 0

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
            return cmd_demo(engine, settings, quick=args.quick, blueprint=args.blueprint)

        errors = settings.validate()
        for e in errors:
            log.warning(e)
        if not settings.telegram_bot_token:
            log.error("TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env, or run --demo / --api.")
            return 1
        if not settings.telegram_allowed_user_id:
            log.error("TELEGRAM_ALLOWED_USER_ID is not set — every command would be rejected.")
            return 1

        # Optionally co-host the authenticated Android API sharing the SAME Orchestrator & Database
        if args.with_api or settings.enable_api_server:
            import threading
            from integrations.api.server import create_api_server
            from integrations.github.api import GitHubAPI
            from integrations.github.executor import GitHubExecutionService

            gh_svc = GitHubExecutionService(engine.db, GitHubAPI(settings.github_token), engine.approvals)
            try:
                api_srv = create_api_server(
                    engine.db,
                    host=args.api_host,
                    port=args.api_port,
                    api_token=settings.api_auth_token,
                    github_service=gh_svc,
                    engine=engine,
                    tls_cert=args.tls_cert,
                    tls_key=args.tls_key,
                    max_upload_mb=settings.max_upload_mb,
                )
            except ValueError as ve:
                log.error("%s", ve)
                return 1
            threading.Thread(target=api_srv.serve_forever, daemon=True, name="android-api-server").start()
            scheme = "https" if (args.tls_cert and args.tls_key) else "http"
            log.info("Shared Android API co-hosted on %s://%s:%d", scheme, args.api_host, api_srv.server_address[1])

        from integrations.telegram.bot import TelegramBot
        handler = TelegramHandler(engine, settings)
        bot = TelegramBot(
            settings.telegram_bot_token,
            settings.telegram_allowed_user_id,
            handler,
            settings.max_upload_mb,
        )
        handler.bind_bot(bot, settings.telegram_allowed_user_id)

        # Recover any interrupted tasks left over from a Termux restart/kill
        rec = engine.startup_recovery()
        if rec["interrupted"]:
            log.info("startup recovery requeued %d task(s)", rec["interrupted"])

        _write_pid()

        def _shutdown(signum, frame):
            log.info("signal %s — shutting down gracefully", signum)
            bot.stop()
            engine.shutdown()
            _remove_pid()
            sys.exit(0)

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)
        log.info("listening for Telegram updates; workers start on demand")
        bot.run_forever()
        return 0
    finally:
        if api_srv is not None:
            try:
                api_srv.shutdown()
                api_srv.server_close()
            except Exception:
                pass
        _remove_pid()
        engine.shutdown()


if __name__ == "__main__":
    sys.exit(main())
