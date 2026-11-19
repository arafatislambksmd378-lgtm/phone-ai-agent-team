# Configuration Reference

All runtime settings are loaded from environment variables or `./.env` via `config/settings.py`. Keep `.env` at `0600` permissions (`chmod 600 .env`) and never commit it to Git.

---

## Telegram Settings

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `""` | Bot token from `@BotFather`. Required for `main.py` listener mode. |
| `TELEGRAM_ALLOWED_USER_ID` | `0` | Single authorized numeric Telegram user ID. Updates and inline button clicks from any other user ID are rejected and audited. |

---

## AI Model Router Settings

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | `""` | Google Gemini API key. |
| `AI_MODEL_DEFAULT` | `gemini-3.5-flash-lite` | Default fallback model identifier. |
| `AI_MODEL_GEMINI` | `gemini-3.5-flash-lite` | Gemini model used when routing to `gemini`. |
| `OPENAI_API_KEY` | `""` | OpenAI API key. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Base URL for OpenAI or any OpenAI-compatible provider (Groq, Together, DeepSeek, OpenRouter, etc.). |
| `AI_MODEL_OPENAI` | `gpt-4o-mini` | Model used by the `openai` adapter. |
| `CODEX_API_KEY` | `""` | Optional dedicated coding/Codex endpoint API key. |
| `CODEX_BASE_URL` | `https://api.openai.com/v1` | Base URL for the `codex` adapter. |
| `AI_MODEL_CODEX` | `gpt-4o` | Model used by the `codex` adapter. |
| `ANTHROPIC_API_KEY` | `""` | Anthropic Claude API key. |
| `AI_MODEL_ANTHROPIC` | `claude-3-5-haiku-latest` | Model used by the `anthropic` adapter. |
| `AI_PROVIDER_ORDER` | `gemini,openai,anthropic` | Comma-separated provider priority list (may include `codex` or custom registered providers). |
| `ALLOW_PROVIDER_FALLBACK` | `true` | Automatically fall over to the next configured provider on transient HTTP/rate-limit errors. |
| `AI_TIMEOUT_SECONDS` | `60` | Per-request HTTP timeout in seconds. |
| `AI_MAX_TOKENS` | `4096` | Maximum output tokens requested per generation call. |

> **Offline / Deterministic Mode**: When no provider API key is set, `ModelRouter.mode()` reports `mock` and agents fall back to clean, project-aware deterministic implementations so tests and `--demo` work offline without fabricating live API telemetry.

---

## GitHub & External Build Settings

| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN` | `""` | Personal access token or fine-grained repo token. Never embedded into `.git/config`; passed only via ephemeral `http.extraHeader` during `push`/`pull`. |
| `GITHUB_REMOTE_REPO` | `""` | `owner/repo` slug or HTTPS GitHub URL. |
| `EXTERNAL_BUILD_URL` | `""` | Optional remote build webhook URL for heavy Android APK / desktop binary builds. |

---

## Android 14 Resource & Execution Limits

| Variable | Default | Description |
|---|---|---|
| `MAX_CONCURRENT_WORKERS` | `1` | Clamped to `1..2`. Default is `1`. If set to `2`, the 2nd worker only claims tasks when `ResourceManager` confirms free RAM ≥ `MIN_FREE_RAM_MB_WORKER2`. |
| `MIN_FREE_RAM_MB_WORKER2` | `600` | Minimum available system RAM (MB) required before a secondary worker thread is allowed to execute. |
| `MIN_FREE_DISK_MB` | `250` | Minimum free workspace storage (MB) required before starting a task. |
| `TASK_TIMEOUT_SECONDS` | `600` | Hard wall-clock timeout per agent task. |
| `STALE_TASK_SECONDS` | `900` | Reclaim threshold for crashed/hung `EXECUTING` worker locks. |

---

## Security, Upload & Approval Limits

| Variable | Default | Description |
|---|---|---|
| `MAX_UPLOAD_MB` | `20` | Maximum inbound Telegram upload size and outbound ZIP artifact size (MB). |
| `MAX_ZIP_MB` | `50` | Maximum total uncompressed bytes extracted from a blueprint `.zip`. |
| `MAX_ZIP_FILES` | `500` | Maximum file count inside an uploaded `.zip`. |
| `APPROVAL_TTL_SECONDS` | `3600` | Expiration window (seconds) for pending human approval requests. |
| `PAID_COST_APPROVAL_USD` | `5.0` | Cumulative per-project live AI spend (USD) that triggers a mandatory `paid_api_usage` human approval (`0` disables). |

---

## Storage & Logging

| Variable | Default | Description |
|---|---|---|
| `WORKSPACE_ROOT` | `./projects` | Root directory where isolated per-project workspaces are created. |
| `DB_PATH` | `./data/phone_agent.db` | SQLite WAL database path. |
| `LOG_LEVEL` | `INFO` | Python log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `LOG_FILE` | `./logs/agent.log` | Rotating redacted log file (`1 MB × 2` backups). |
