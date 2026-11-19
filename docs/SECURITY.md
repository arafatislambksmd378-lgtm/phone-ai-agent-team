# Security & Approval Enforcement

## 1. Security Model & Trust Boundaries

1. **Single-Owner Telegram Authorization (`integrations/telegram/bot.py`)**:
   - Every inbound `message`, `edited_message`, and `callback_query` is verified against `TELEGRAM_ALLOWED_USER_ID`.
   - Unauthorized users receive a generic `⛔ Not authorized.` notice; unauthorized inline approval taps are rejected with `show_alert=True` and logged as `UNAUTHORIZED_ATTEMPT` in `approval_audit_logs`.
2. **Untrusted Blueprints & Uploads**:
   - Uploaded documents (`.txt`, `.md`, `.json`, `.csv`, `.pdf`, `.docx`, `.zip`, code files) and Telegram text are **never executed automatically**.
   - `DocumentProcessor` parses content using pure Python stdlib readers (`zipfile` + `xml.etree.ElementTree` for `.docx`, stream regex extraction for `.pdf`), stripping control characters and clamping text size.
   - `safe_extract_zip` blocks `../` path traversal, absolute paths, POSIX symlink entries (`0o120000`), executable binaries (`.exe`, `.dll`, `.so`, `.apk`, `.dex`, `.jar`), archives exceeding `MAX_UPLOAD_MB`, > `MAX_ZIP_FILES` entries, > `MAX_ZIP_MB` uncompressed total, and > 100:1 compression-ratio bombs.
3. **Workspace Jail (`execution/file_manager.py` & `security/validation.py`)**:
   - All agent file reads, writes, and deletes resolve through `validate_workspace_path`, which blocks `..`, null bytes, and symlink escapes outside the per-project workspace root.
4. **Command Execution Allowlist (`security/validation.py` & `execution/command_runner.py`)**:
   - Commands execute via `subprocess.Popen(argv, shell=False, start_new_session=True)` — never through `/bin/sh -c`.
   - Shell metacharacters (`;`, `&&`, `||`, `` ` ``, `$()`, pipes, redirects), `sudo`, `su`, `rm -rf /`, `curl | sh`, `git push --force`, `dd`, `mkfs`, and `eval` are blocked before process spawn.
5. **Secret Redaction (`security/secret_manager.py` & `integrations/github/client.py`)**:
   - OpenAI (`sk-...`), GitHub (`ghp_...`, `github_pat_...`), Google (`AIza...`), Slack (`xox...`), Telegram bot tokens, and Bearer headers are automatically redacted from console logs, rotating log files, SQLite `execution_logs`, subprocess output, and outbound Telegram messages.
   - `GitHubClient` scans workspaces for leaked secrets prior to remote push and passes `GITHUB_TOKEN` only as an ephemeral `git -c http.extraHeader=...` header so tokens are never persisted in `.git/config`.

---

## 2. Execution-Layer Human Approval Gate (`security/approval.py`)

High-impact actions **cannot be bypassed by prompts or agent outputs** because `ApprovalManager.require_approval(...)` is enforced inside `CommandRunner`, `FileManager`, `GitHubClient`, and `OrchestratorEngine`:

| Gate Action | Enforced At | Trigger |
|---|---|---|
| `production_deploy` | `CommandRunner` / `DevOpsAgent` | Any live deployment command or `deploy=True` workflow |
| `destructive_file_op` | `FileManager.delete` / `CommandRunner` | Deleting protected/populated project files or `rm` invocations |
| `database_delete` | `CommandRunner` | Destructive SQL (`DROP TABLE`, `TRUNCATE`, `DELETE FROM` without `WHERE`) |
| `github_push_public` | `GitHubClient.push` / `CommandRunner` | Pushing commits or private workspaces to a remote/public repository |
| `public_github_release` | `ReleaseManagerAgent` / `CommandRunner` | Publishing tags or GitHub releases |
| `external_account_change` | `CommandRunner` | Modifying external cloud/account state |
| `paid_api_usage` | `OrchestratorEngine._execute_task` | Live AI spend on a project reaching `PAID_COST_APPROVAL_USD` |
| `secret_operation` | `FileManager.write` / `CommandRunner` | Creating or overwriting `.env` / `*.pem` / `*.key` files |

### Approval Lifecycle & Audit Protections
1. When triggered without a valid `approval_id`, `ApprovalManager` creates a `PENDING` record bound to `project_id`, `task_id`, `action`, and `expires_at` (`now + APPROVAL_TTL_SECONDS`).
2. `TelegramHandler` immediately sends an inline keyboard prompt (`✅ Approve` / `❌ Reject`) to the authorized owner.
3. Duplicate callback taps, expired requests (`EXPIRED`), and already-consumed single-use approvals (`CONSUMED`) are rejected deterministically and recorded in `approval_audit_logs`.
