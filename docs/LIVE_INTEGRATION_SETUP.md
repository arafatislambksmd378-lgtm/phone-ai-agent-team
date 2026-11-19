# Live Integration Setup (owner-authorized, secure credential entry)

This batch connects **Google Gemini** (first provider) and the **Telegram Bot** through a manual
GitHub Actions workflow. Credentials are entered in GitHub's secret store — never in chat, a PR,
an issue, a source file or a log. No credential is available in the coding sandbox, and outbound
provider/Telegram hosts are blocked there, so all live calls run on a GitHub-hosted runner.

## 1. Configure credentials in GitHub (do this yourself)

In the repository, open **Settings → Secrets and variables → Actions → New repository secret**.
Add exactly these names:

| Name | Type | Value | Needed for |
|---|---|---|---|
| `GEMINI_API_KEY` | Secret | Your Google Gemini API key | All live AI checks |
| `TELEGRAM_BOT_TOKEN` | Secret | Token from BotFather | Telegram checks |
| `TELEGRAM_ALLOWED_USER_ID` | Secret | Your numeric Telegram user ID | Telegram authorization |

Optional non-secret configuration: create a repository **variable** named `GEMINI_MODEL`
(default `gemini-2.0-flash` if omitted).

The Android API token used by the sync test is generated randomly inside the runner; it is not a
repository secret and is never printed. Do **not** add `API_AUTH_TOKEN`, GitHub tokens or any
other secret for this workflow.

## 2. Verify no-charge before running

The owner selected **no paid usage**. The workflow exposes a boolean input
`free_tier_confirmed`; it fails closed if unchecked. Check that your specific key and model are
covered by a no-charge allowance in your provider account. If you are unsure, leave it unchecked
and report the uncertainty — no live call will be made.

Only the `gemini` provider is selectable in this batch. OpenAI, Codex-compatible and Anthropic
adapters remain implemented but are deliberately not activated or billed.

## 3. Run the workflow

1. Open the **Actions** tab.
2. Select **Live Integration Verification (owner-authorized, manual)**.
3. Click **Run workflow**, choose branch `arena/3d5b512c-phone-ai-agent-team`.
4. Check `free_tier_confirmed` only after step 2.
5. Keep `run_telegram` checked to test the bot; set `run_agent_build` if you also want `/build`
   to plan and execute a small live project (more model calls, still subject to the no-charge rule).

The runner will send a small number of real Telegram messages to your authorized chat
(`/start`, `/help`, `/new`, `/status`, `/tasks`, `/agents`, `/pending`, `/task`, `/sync`,
an inline approval prompt, a callback answer and a test ZIP). Your account must have started the
bot at least once for delivery to succeed.

## 4. Read the evidence

- Run summary: `live-evidence/*.md` is appended to the workflow summary.
- Artifact: `live-integration-evidence` (redacted reports only).
- Reports contain masked key hints (e.g. `AIza••••789`), provider/model names, token counts,
  cost estimates, task/approval IDs and PASS/FAIL lines. They never contain full credentials.
- A failing step produces a failed run with the exact classification (`AUTH_ERROR`, `TIMEOUT`,
  `RATE_LIMITED`, etc.). Do not interpret a skipped or failed check as a pass.

## 5. Change credentials later

Update or rotate the values in **Settings → Secrets and variables → Actions** and re-run the
workflow. No source change or redeploy is needed. Removing a secret makes the next preflight fail
closed. For local Termux operation, set the same keys in `.env` (`chmod 600 .env`) instead; the
Android/Termux process reads `.env` at startup, so restart it after a local rotation.

## 6. Restart the system locally (on your device)

```sh
cd phone-ai-agent-team
./scripts/stop_termux.sh 2>/dev/null || true
nano .env                      # only if you are configuring locally; chmod 600 .env
./scripts/start_termux.sh --daemon
./scripts/healthcheck.sh
# Android + Telegram sharing one backend/orchestrator/DB:
API_AUTH_TOKEN=<your-token> python3 main.py --with-api --api-port 8000
```

Use HTTPS (reverse proxy or tunnel) for the Android client; never expose the setup portal over
public HTTP. `scripts/start_web_setup.sh` binds `0.0.0.0` and must not be used for secrets
without a hardened authenticated HTTPS front end.
