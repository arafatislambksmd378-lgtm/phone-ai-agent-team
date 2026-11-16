# Security model

This document states what the system protects, how, and what it deliberately does **not**
claim.

## Threat model

| Asset | Threat | Control |
|---|---|---|
| Provider API keys | exfiltration via logs, Telegram, generated code | keys live only in `.env`/environment; every log and chat message passes `redact()`; adapter `__repr__` masks the key; subprocesses get a scrubbed environment |
| Host filesystem | writes/reads outside a project workspace | all paths resolved and checked with `os.path.commonpath` against the workspace root |
| Host shell | arbitrary command execution | allowlist, `shell=False`, no inline-eval flags, no shell metacharacters, scrubbed env |
| Uploaded archives | path traversal / zip bomb | traversal + absolute-path rejection, member count, uncompressed size and ratio limits, streamed extraction with declared-size checks |
| Telegram channel | impersonation / unauthorized control | single allow-listed numeric user id; unset ⇒ deny everything |
| Destructive actions | accidental push/deploy/delete | approval rows with single-shot decisions |

## Command policy (fail-closed)

`config/permissions.py` decides `(allowed, needs_approval)`.

- **Blocked outright**: `sh`, `bash`, `sudo`, `env`, `xargs`, `eval`, `exec`, `dd`,
  `mkfs`, `systemctl`, `nc`, `socat`, `crontab`, power/reboot programs.
- **Blocked outright**: inline evaluation — `python3 -c`, `node -e`.
- **Blocked outright**: shell metacharacters (`;`, `|`, `&`, `>`, `<`, backtick, `$(`, `${`,
  newline, NUL). The runner never uses a shell, so these are either meaningless or an
  injection attempt.
- **Approval required**: `rm`, `mv`, `cp`, `chmod`, `chown`, `pip install`, `git push`,
  `git reset`, `git clean`, `git remote`, `curl`, `wget`, `ssh`, `docker`, cloud CLIs.
- **Safe**: `ls`, `cat`, `head`, `tail`, `wc`, `file`, `echo`, `pwd`, `find`, `grep`,
  `python3 <file>`, `pytest`, `git status`, `git diff`, `node <file>`, `npm run`.
- **Unknown program**: denied.

## Upload pipeline

1. `validate_upload` — name, extension allowlist, non-zero size, size cap.
2. `TelegramBot.download_file` — streamed to `*.part` with a hard byte cap; deleted on
   abort; no partial files left behind.
3. `validate_upload_content` — magic bytes (`%PDF`, `PK\x03\x04`) must match the extension;
   a renamed `.exe` is rejected.
4. `safe_extract_zip` — traversal, absolute path, member count, total size, compression
   ratio, and a per-member declared-size check during the stream.

## Approval workflow

Actions in `security.approval.DANGEROUS_ACTIONS` (`git_push`, `deploy`, `publish`,
`delete_files`, `modify_outside_workspace`, `install_packages`, `spend_money`,
`change_external_account`, `expose_credentials`, `destructive_command`) create an
approval row. `decide_approval` only updates rows still `PENDING`, so a decision cannot be
flipped. A project with an open approval reports `WAITING_FOR_APPROVAL` instead of
`COMPLETED`.

## Secret hygiene

Patterns redacted: `sk-…`, `sk-ant-…`, `AIza…`, Telegram bot tokens (`8–10 digits:35+
chars`), `ghp_/gho_/ghu_/ghs_/ghr_` and `github_pat_…`, `Bearer <token>`, and any
`api_key|token|secret|password = value` assignment.

Redaction is applied at the boundaries: `Database.log`, `TelegramBot.send_message`,
`CommandRunner` output, and `OrchestratorEngine._notify`. It is a safety net, not a
sandbox — code that intentionally writes secrets into a file inside its own workspace is
still possible, which is why generated projects never receive provider keys.

## What this system does NOT protect against

- **A malicious model.** AI output is untrusted text. It is written to files inside the
  workspace and never executed automatically; commands found in AI output are not run.
- **A compromised phone.** Root, malware or someone with Termux access can read `.env`.
- **Network attackers on a public Wi-Fi.** All provider calls use TLS; the generated
  sample app serves plain HTTP locally and should not be exposed to the internet without a
  reverse proxy.
- **Supply-chain compromise of the base system.** Only the Python standard library is
  used at runtime (`pytest` is dev-only), which keeps the surface small but not zero.

## Reporting

Security issues: open an issue marked `security` in the repository, or (for sensitive
reports) contact the maintainer privately. Please do not include live keys in reports.
