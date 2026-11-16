# Tests

Run everything:

```sh
python3 -m pytest -q          # 136 tests, ~5 s, no network, no API keys
```

## Requirement → test mapping

| Audit requirement | Test file | Notes |
|---|---|---|
| 1. All 16 agents individually | `test_all_agents.py::test_each_agent_runs` | parametrised over the registry |
| 2. Agent-to-agent handoffs | `test_all_agents.py::test_pipeline_artifacts_are_real` | sequential run, asserts real artifacts |
| 3. Complete orchestration workflow | `test_orchestrator.py::test_full_pipeline_runs_all_16` | end to end, mock mode |
| 4. Task dependency handling | `test_orchestrator.py::test_dependency_ordering_is_respected` | deps all `COMPLETED` before dependents |
| 5. SQLite persistence | `test_recovery.py::test_database_survives_reopen` | reopen, same data |
| 6. Interrupted task recovery | `test_recovery.py::test_recover_interrupted_*` | tasks requeued, executions `INTERRUPTED` |
| 7. Duplicate task prevention | `test_orchestrator.py::test_claimed_task_is_never_handed_out_twice`, `test_planning_twice_does_not_duplicate` | atomic claim + idempotent plan |
| 8. Telegram authorization | `test_telegram.py::test_unauthorized_user_is_refused` | non-owner gets refusal, no state change |
| 9. File upload validation | `test_security.py::test_allowed_uploads`, `test_upload_size_limit`, `test_content_sniffing_*` | extension, size, magic bytes |
| 10. ZIP traversal protection | `test_security.py::test_zip_*` | traversal, absolute path, count, size, corrupt |
| 11. Command allowlist | `test_security.py::test_dangerous_commands_blocked` (+ safe/approval lists) | includes `python3 -c` |
| 12. Secret redaction | `test_security.py::test_secret_redaction` (7 params), `test_logs_are_redacted` | keys + bot tokens + GH tokens |
| 13. Approval / rejection | `test_approvals.py` (6 tests) | single-shot decisions, pending blocks completion |
| 14. Worker shutdown | `test_orchestrator.py::test_workers_stop_after_completion` | thread count returns to baseline |
| 15. Provider failure + fallback | `test_providers.py::test_error_classification`, `test_router.py::test_fallback_disabled_only_tries_first_provider` | |
| 16. Missing API key | `test_providers.py::test_not_configured_adapters`, `test_router.py::test_mock_mode_without_keys` | `NOT_CONFIGURED` / `mock` |
| 17. Invalid model configuration | `test_providers.py::test_missing_model_is_reported` | `BAD_REQUEST`, not a crash |
| 18. Timeout / retry | `test_providers.py::test_timeout_is_classified`, `test_recovery.py::test_retry_then_permanent_failure` | |
| 19. Git checkpoint creation | `test_all_agents.py` + demo run (`git -C projects/<ws> log`) | 16 commits in a full run |
| 20. Installation script | `bash -n scripts/*.sh` + `main.py --selfcheck` | script refuses to run outside Termux |

## Deliberately excluded

- Real network calls to AI providers (would need keys and money). Adapters are tested by
  monkeypatching the transport.
- Real Telegram API calls (would need a token). The bot is tested through a fake transport.
- Android-specific behaviour (Termux kill policy, wake locks) — see
  `docs/operations.md` for the on-device procedure.
