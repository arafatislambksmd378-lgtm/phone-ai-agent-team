"""Telegram authorization and command surface — no network required."""
import pytest

from integrations.telegram.bot import TelegramBot, is_authorized


def update(user_id, text="/start", chat_id=42):
    return {"update_id": 1,
            "message": {"message_id": 1, "date": 0, "text": text,
                        "chat": {"id": chat_id, "type": "private"},
                        "from": {"id": user_id, "is_bot": False, "first_name": "U"}}}


def test_authorized_user_only():
    assert is_authorized(update(7), 7) is True
    assert is_authorized(update(8), 7) is False
    assert is_authorized(update(7), 0) is False          # unset allow-list denies all
    assert is_authorized({}, 7) is False


class FakeBot(TelegramBot):
    def __init__(self):
        super().__init__("123:ABC", 7, handler=None)
        self.sent: list[tuple[int, str]] = []

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return {"ok": True}


@pytest.fixture()
def handler(tmp_path):
    from config.settings import Settings
    from integrations.telegram.handler import TelegramHandler
    from orchestrator.engine import OrchestratorEngine
    s = Settings(telegram_bot_token="123:ABC", telegram_allowed_user_id=7,
                 workspace_root=tmp_path / "ws", db_path=tmp_path / "t.db")
    engine = OrchestratorEngine(s)
    h = TelegramHandler(engine, s)
    yield FakeBot(), h, engine
    engine.shutdown()


def test_unauthorized_user_is_refused(handler):
    bot, h, _ = handler
    h(bot, update(999, "/new pwn"))
    assert bot.sent and "Not authorized" in bot.sent[0][1]


def test_help_and_agents(handler):
    bot, h, _ = handler
    h(bot, update(7, "/help"))
    assert "commands" in bot.sent[-1][1].lower()
    h(bot, update(7, "/agents"))
    assert "Chief AI Orchestrator" in bot.sent[-1][1]


def test_new_project_and_status(handler):
    bot, h, engine = handler
    h(bot, update(7, "/new auditproj"))
    pid = h.current[42]
    assert engine.db.get_project(pid) is not None
    h(bot, update(7, f"/status {pid}"))
    assert "auditproj" in bot.sent[-1][1]


def test_unknown_command_is_treated_as_requirements(handler):
    bot, h, engine = handler
    h(bot, update(7, "/new p"))
    pid = h.current[42]
    h(bot, update(7, "Must support offline mode"))
    req = engine.db.get_project(pid)
    from execution.file_manager import FileManager
    assert "offline" in FileManager(req["workspace"]).read("requirements.txt")


def test_text_before_project_asks_for_new(handler):
    bot, h, _ = handler
    h(bot, update(7, "random text"))
    assert "/new" in bot.sent[-1][1]


def test_projects_settings_health(handler):
    bot, h, _ = handler
    for cmd in ("/projects", "/settings", "/health"):
        h(bot, update(7, cmd))
    joined = " ".join(t for _, t in bot.sent)
    assert "No projects" in joined or "project" in joined.lower()
    assert "Workers:" in joined
    assert "AI mode" in joined


def test_secrets_never_returned_by_settings(handler):
    bot, h, engine = handler
    engine.settings.gemini_api_key = "AIzaSECRETSECRETSECRETSECRET12"
    h(bot, update(7, "/settings"))
    assert "SECRETSECRET" not in bot.sent[-1][1]


def test_document_before_project_is_refused(handler):
    bot, h, _ = handler
    msg = {"update_id": 2, "message": {"chat": {"id": 42}, "from": {"id": 7},
                                       "document": {"file_id": "f", "file_name": "a.txt", "file_size": 10}}}
    h(bot, msg)
    assert "/new" in bot.sent[-1][1]


def test_approve_unknown_id(handler):
    bot, h, _ = handler
    h(bot, update(7, "/approve appr_nope"))
    assert "No pending approval" in bot.sent[-1][1]


def test_message_truncation_and_redaction():
    class Capture(TelegramBot):
        def __init__(self):
            super().__init__("1:A", 1, None)
            self.payload = None

        def _api(self, method, payload=None, timeout=30):
            self.payload = payload
            return {"ok": True}

    bot = Capture()
    bot.send_message(1, "x" * 10000 + " sk-abcdefghijklmnopqrstuvwx")
    assert len(bot.payload["text"]) <= 4000
    assert "abcdefghijklmnopqrstuvwx" not in bot.payload["text"]
