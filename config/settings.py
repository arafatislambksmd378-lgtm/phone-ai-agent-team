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


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _str(name: str, default: str) -> str:
    return os.getenv(name, default)


KNOWN_BUILTIN_PROVIDERS = {"gemini", "openai", "anthropic", "codex"}


@dataclass
class Settings:
    telegram_bot_token: str = field(default_factory=lambda: _str("TELEGRAM_BOT_TOKEN", ""))
    telegram_allowed_user_id: int = field(default_factory=lambda: _int("TELEGRAM_ALLOWED_USER_ID", 0))
    gemini_api_key: str = field(default_factory=lambda: _str("GEMINI_API_KEY", ""))
    openai_api_key: str = field(default_factory=lambda: _str("OPENAI_API_KEY", ""))
    openai_base_url: str = field(default_factory=lambda: _str("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    codex_api_key: str = field(default_factory=lambda: _str("CODEX_API_KEY", ""))
    codex_base_url: str = field(default_factory=lambda: _str("CODEX_BASE_URL", "https://api.openai.com/v1"))
    anthropic_api_key: str = field(default_factory=lambda: _str("ANTHROPIC_API_KEY", ""))
    ai_provider_order: list[str] = field(default_factory=lambda: [
        p.strip() for p in _str("AI_PROVIDER_ORDER", "gemini,openai,anthropic").split(",") if p.strip()
    ])
    # Per-provider model names (configurable — never hardcoded in adapters).
    ai_model_default: str = field(default_factory=lambda: _str("AI_MODEL_DEFAULT", "gemini-2.5-flash"))
    ai_model_gemini: str = field(default_factory=lambda: _str("AI_MODEL_GEMINI", _str("AI_MODEL_DEFAULT", "gemini-2.5-flash")))
    ai_model_openai: str = field(default_factory=lambda: _str("AI_MODEL_OPENAI", "gpt-4o-mini"))
    ai_model_codex: str = field(default_factory=lambda: _str("AI_MODEL_CODEX", "gpt-4o"))
    ai_model_anthropic: str = field(default_factory=lambda: _str("AI_MODEL_ANTHROPIC", "claude-3-5-haiku-latest"))
    ai_max_tokens: int = field(default_factory=lambda: max(256, _int("AI_MAX_TOKENS", 4096)))
    allow_provider_fallback: bool = field(default_factory=lambda: _bool("ALLOW_PROVIDER_FALLBACK", True))
    max_concurrent_workers: int = field(default_factory=lambda: max(1, min(2, _int("MAX_CONCURRENT_WORKERS", 1))))
    task_timeout_seconds: int = field(default_factory=lambda: _int("TASK_TIMEOUT_SECONDS", 600))
    ai_timeout_seconds: int = field(default_factory=lambda: _int("AI_TIMEOUT_SECONDS", 60))
    stale_task_seconds: int = field(default_factory=lambda: max(60, _int("STALE_TASK_SECONDS", 900)))
    retry_backoff_base_seconds: int = field(default_factory=lambda: max(1, _int("RETRY_BACKOFF_BASE_SECONDS", 2)))
    retry_backoff_max_seconds: int = field(default_factory=lambda: max(1, _int("RETRY_BACKOFF_MAX_SECONDS", 300)))
    memory_versions_per_key: int = field(default_factory=lambda: max(2, min(200, _int("MEMORY_VERSIONS_PER_KEY", 25))))
    approval_ttl_seconds: int = field(default_factory=lambda: max(60, _int("APPROVAL_TTL_SECONDS", 3600)))
    paid_cost_approval_usd: float = field(default_factory=lambda: max(0.0, _float("PAID_COST_APPROVAL_USD", 5.0)))
    min_free_ram_mb_worker2: int = field(default_factory=lambda: max(128, _int("MIN_FREE_RAM_MB_WORKER2", 600)))
    min_free_disk_mb: int = field(default_factory=lambda: max(50, _int("MIN_FREE_DISK_MB", 250)))
    github_token: str = field(default_factory=lambda: _str("GITHUB_TOKEN", ""))
    github_remote_repo: str = field(default_factory=lambda: _str("GITHUB_REMOTE_REPO", ""))
    external_build_url: str = field(default_factory=lambda: _str("EXTERNAL_BUILD_URL", ""))
    api_auth_token: str = field(default_factory=lambda: _str("API_AUTH_TOKEN", ""))
    api_host: str = field(default_factory=lambda: _str("API_HOST", "127.0.0.1"))
    api_port: int = field(default_factory=lambda: max(1, min(65535, _int("API_PORT", 8000))))
    api_tls_cert: str = field(default_factory=lambda: _str("API_TLS_CERT", ""))
    api_tls_key: str = field(default_factory=lambda: _str("API_TLS_KEY", ""))
    enable_api_server: bool = field(default_factory=lambda: _bool("ENABLE_API_SERVER", False))
    web_setup_token: str = field(default_factory=lambda: _str("WEB_SETUP_TOKEN", ""))
    workspace_root: Path = field(default_factory=lambda: Path(_str("WORKSPACE_ROOT", "./projects")))
    db_path: Path = field(default_factory=lambda: Path(_str("DB_PATH", "./data/phone_agent.db")))
    log_level: str = field(default_factory=lambda: _str("LOG_LEVEL", "INFO"))
    log_file: Path = field(default_factory=lambda: Path(_str("LOG_FILE", "./logs/agent.log")))
    max_upload_mb: int = field(default_factory=lambda: _int("MAX_UPLOAD_MB", 20))
    max_zip_mb: int = field(default_factory=lambda: _int("MAX_ZIP_MB", 50))
    max_zip_files: int = field(default_factory=lambda: _int("MAX_ZIP_FILES", 500))

    def validate(self) -> list[str]:
        errs = []
        if not self.telegram_bot_token:
            errs.append("TELEGRAM_BOT_TOKEN missing")
        if not self.telegram_allowed_user_id:
            errs.append("TELEGRAM_ALLOWED_USER_ID missing (all commands will be rejected)")
        if not any([self.gemini_api_key, self.openai_api_key, self.codex_api_key, self.anthropic_api_key]):
            errs.append("No AI provider API key configured (deterministic MOCK mode will be used)")
        for name in self.ai_provider_order:
            if name not in KNOWN_BUILTIN_PROVIDERS:
                errs.append(f"Unknown provider in AI_PROVIDER_ORDER: {name}")
        return errs

    def ensure_dirs(self) -> None:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        Path("logs").mkdir(exist_ok=True)
        Path("backups").mkdir(exist_ok=True)


def get_settings() -> Settings:
    return Settings()
