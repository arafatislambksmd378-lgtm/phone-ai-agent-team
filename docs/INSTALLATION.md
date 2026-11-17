# Installation Guide (Android 14 Termux & Linux)

## Target Hardware Profile

- **Primary Target**: Android 14 smartphone, **3 GB RAM**, **64 GB storage**, running **Termux** (F-Droid build recommended; Google Play builds are deprecated).
- **Supported Secondary Hosts**: Standard Linux / WSL2 environments with Python 3.11+ and `git`.
- **Forbidden Heavy Dependencies**: No Docker, no PostgreSQL, no Redis, no local LLM weights, and no native C/Rust pip compilations required at runtime.

---

## 1. Install Termux on Android 14

1. Install **Termux** (v0.118+) and optionally **Termux:API** from **F-Droid**.
2. Disable Android battery optimization ("Unrestricted" battery usage) for Termux so Android 14 Phantom Process Killer / Doze does not suspend long-polling or active builds.
3. Open Termux and clone this repository:

```bash
pkg update -y && pkg install -y git python
git clone https://github.com/arafatislambksmd378-lgtm/phone-ai-agent-team.git
cd phone-ai-agent-team
```

---

## 2. Run the Repeatable Installer

```bash
./scripts/install_termux.sh
```

What `install_termux.sh` does safely on every run:
1. Installs `python` and `git` via `pkg` when inside Termux (or verifies `python3` and `git` on Linux).
2. Creates an optional lightweight `.venv` if `venv` is available.
3. Creates `./data`, `./logs`, `./backups`, and `./projects` with restricted permissions.
4. Copies `.env.example` → `.env` (`chmod 600 .env`) **only if `.env` does not already exist**.
5. Sets a local fallback Git author (`agent@phone-local`) if none is configured so local workspace checkpoints never fail.
6. Runs `python3 main.py --selfcheck`.

---

## 3. Configure `.env`

Edit `.env` (`nano .env`) and populate:
1. `TELEGRAM_BOT_TOKEN` — obtained from `@BotFather` on Telegram.
2. `TELEGRAM_ALLOWED_USER_ID` — your numeric Telegram user ID (send `/start` to `@userinfobot`).
3. At least one AI provider API key (`GEMINI_API_KEY`, `OPENAI_API_KEY`, `CODEX_API_KEY`, or `ANTHROPIC_API_KEY`).

Verify your configuration:

```bash
./scripts/healthcheck.sh
```

---

## 4. Starting and Stopping the System

### Local Smoke Run (No Telegram Required)
```bash
python3 main.py --demo --quick
```

### Foreground Mode (Interactive Termux Session)
```bash
./scripts/start_termux.sh
```

### Background Daemon Mode (Acquires `termux-wake-lock` + PID file)
```bash
./scripts/start_termux.sh --daemon
```

### Graceful Stop (Releases `termux-wake-unlock`)
```bash
./scripts/stop_termux.sh
```

---

## 5. Android 14 Specific Notes

- **Android SDK / Gradle Builds**: Building signed `.apk`/`.aab` binaries directly on a 3 GB RAM phone will trigger the Linux OOM killer if Gradle spawns a multi-GB JVM daemon. When generating an `android_app` blueprint, the system creates complete Kotlin/Gradle source files on-device and generates GitHub Actions CI workflows (`EXTERNAL_BUILD_URL` or GitHub Actions) for cloud APK compilation unless native Android SDK tools are explicitly installed and memory permits.
- **Desktop GUI Apps**: Termux runs headless by default; generated Tkinter/desktop apps include headless model/controller unit tests so QA passes reliably without an X11 display.
