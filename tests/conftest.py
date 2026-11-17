"""Mark legacy host-execution expectations as blocked, without masking them as passes.

The tests still run. Strict xfail makes an unexpected pass fail CI until the
expectations and the isolated worker have been reviewed together.
"""
import pytest

_BLOCKED = {
    "tests/test_agents.py::test_all_agents_runnable",
    "tests/test_all_agents.py::test_each_agent_runs[workspace_executor]",
    "tests/test_all_agents.py::test_pipeline_artifacts_are_real",
    "tests/test_handoffs_and_workflows.py::test_ai_powered_custom_code_generation_and_debugger_repair_loop",
    "tests/test_handoffs_and_workflows.py::test_e2e_rest_api_workflow_skips_ui_and_packages_zip",
    "tests/test_handoffs_and_workflows.py::test_e2e_cli_and_android_and_desktop_workflows",
    "tests/test_orchestrator.py::test_full_pipeline_runs_all_16",
    "tests/test_security.py::test_command_runner_enforces_policy",
}


def pytest_collection_modifyitems(items):
    for item in items:
        if item.nodeid in _BLOCKED:
            item.add_marker(pytest.mark.xfail(
                reason="Host execution intentionally blocked pending a verified isolated worker",
                strict=True,
            ))
