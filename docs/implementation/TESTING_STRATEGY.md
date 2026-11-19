# Testing Strategy
pytest (stdlib-mocked GitHub transport — labelled mock, never claimed live), DB migration test from a v3-shaped DB, repo-isolation test, API↔Telegram shared-DB sync test, approval-gate test for merge. Live GitHub/Telegram/Android-device tests remain opt-in (`-m live`) and are reported separately in the final report.
