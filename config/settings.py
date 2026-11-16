"""Central settings — env-driven, Termux-friendly, no heavy deps."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path

def _load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)

_load_dotenv()

def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default

@dataclass
class Settings:
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_allowed_user_id: int = field(default_factory=lambda: _int("TELEGRAM_ALLOWED_USER_ID", 0))
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_base_url: str = field(default_factory=lambda: os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    ai_provider_order: list[str] = field(default_factory=lambda: [p.strip() for p in os.getenv("AI_PROVIDER_ORDER", "gemini,openai,anthropic").split(",") if p.strip()])
    ai_model_default: str = field(default_factory=lambda: os.getenv("AI_MODEL_DEFAULT", "gemini-2.0-flash"))
    max_concurrent_workers: int = field(default_factory=lambda: max(1, min(2, _int("MAX_CONCURRENT_WORKERS", 1))))
    task_timeout_seconds: int = field(default_factory=lambda: _int("TASK_TIMEOUT_SECONDS", 600))
    workspace_root: Path = field(default_factory=lambda: Path(os.getenv("WORKSPACE_ROOT", "./projects")))
    db_path: Path = field(default_factory=lambda: Path(os.getenv("DB_PATH", "./data/phone_agent.db")))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    max_upload_mb: int = field(default_factory=lambda: _int("MAX_UPLOAD_MB", 20))
    max_zip_mb: int = field(default_factory=lambda: _int("MAX_ZIP_MB", 50))
    max_zip_files: int = field(default_factory=lambda: _int("MAX_ZIP_FILES", 500))

    def validate(self) -> list[str]:
        errs = []
        if not self.telegram_bot_token:
            errs.append("TELEGRAM_BOT_TOKEN missing")
        if not self.telegram_allowed_user_id:
            errs.append("TELEGRAM_ALLOWED_USER_ID missing")
        if not any([self.gemini_api_key, self.openai_api_key, self.anthropic_api_key]):
            errs.append("No AI provider API key configured (mock mode will be used)")
        return errs

    def ensure_dirs(self) -> None:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        Path("logs").mkdir(exist_ok=True)
        Path("backups").mkdir(exist_ok=True)

def get_settings() -> Settings:
    return Settings()
