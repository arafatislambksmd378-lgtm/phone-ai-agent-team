"""Tests for Blueprint-driven dynamic agent selection, DAG validation, and replanning."""
from __future__ import annotations

import pytest

from agents.registry import AGENT_MODULES
from memory.database import Database
from orchestrator.planner import BlueprintAnalyzer, TaskPlanner


@pytest.fixture()
def planner_env(tmp_path):
    db = Database(tmp_path / "planner.db")
    ws = tmp_path / "ws"
    ws.mkdir()
    pid = db.create_project("dyn", str(ws))
    planner = TaskPlanner(db, ai_router=None)
    yield db, planner, pid
    db.close()


def test_rest_api_excludes_uiux_and_frontend(planner_env):
    db, planner, pid = planner_env
    blueprint = (
        "Build a headless JSON REST API service for managing warehouse inventory "
        "items and stock levels with SQLite persistence and OpenAPI docs. No frontend UI."
    )
    tids = planner.plan(pid, blueprint)
    tasks = db.list_tasks(pid)
    selected = {t["agent_id"] for t in tasks}

    assert len(tids) == len(tasks)
    assert "uiux_designer" not in selected
    assert "frontend_developer" not in selected
    for needed in (
        "requirement_analyst",
        "software_architect",
        "database_engineer",
        "backend_developer",
        "api_integrator",
        "security_engineer",
        "qa_tester",
        "chief_orchestrator",
    ):
        assert needed in selected

    analysis = db.get_memory(pid)["blueprint_analysis"]
    assert analysis["software_type"] in ("rest_api", "backend_service")
    assert "uiux_designer" in analysis["excluded_agents"]
    assert "frontend_developer" in analysis["excluded_agents"]


def test_cli_tool_selects_lean_cli_pipeline(planner_env):
    db, planner, pid = planner_env
    blueprint = "Create a Python command-line CLI utility using argparse to lint markdown files."
    planner.plan(pid, blueprint)
    selected = {t["agent_id"] for t in db.list_tasks(pid)}

    assert "backend_developer" in selected
    assert "qa_tester" in selected
    assert "uiux_designer" not in selected
    assert "frontend_developer" not in selected
    assert "api_integrator" not in selected
    assert db.get_memory(pid)["blueprint_analysis"]["software_type"] == "cli_tool"


def test_python_library_blueprint_classification():
    analyzer = BlueprintAnalyzer()
    res = analyzer.analyze("Develop a reusable pure Python SDK package and library for date math.")
    assert res.software_type == "python_library"
    assert "frontend_developer" not in res.selected_agents
    assert "uiux_designer" not in res.selected_agents
    assert "backend_developer" in res.selected_agents


def test_android_and_desktop_blueprints_select_appropriate_workflows():
    analyzer = BlueprintAnalyzer()

    android = analyzer.analyze("Build a native Kotlin Android mobile app for tracking daily water intake.")
    assert android.software_type == "android_app"
    assert "uiux_designer" in android.selected_agents
    assert "devops" in android.selected_agents
    assert "frontend_developer" not in android.selected_agents

    desktop = analyzer.analyze("Build a cross-platform Tkinter desktop GUI calculator app.")
    assert desktop.software_type == "desktop_app"
    assert "uiux_designer" in desktop.selected_agents
    assert "frontend_developer" not in desktop.selected_agents


def test_fullstack_web_app_selects_all_16_with_multi_parent_dag(planner_env):
    db, planner, pid = planner_env
    planner.plan(pid, "Build a full-stack web dashboard with HTML frontend, REST API, and SQLite login.")
    tasks = db.list_tasks(pid)
    assert {t["agent_id"] for t in tasks} == set(AGENT_MODULES)
    by_agent = {t["agent_id"]: t for t in tasks}
    chief_deps = db.task_dependencies(by_agent["chief_orchestrator"]["id"])
    assert len(chief_deps) >= 1


def test_cycle_and_unknown_dependency_are_rejected():
    with pytest.raises(ValueError, match="Cycle"):
        TaskPlanner.validate_dag({"a": ["b"], "b": ["a"]})

    with pytest.raises(ValueError, match="unknown"):
        TaskPlanner.validate_dag({"a": ["missing"]})


def test_dynamic_replan_adds_ui_agents_without_duplicating_completed_tasks(planner_env):
    db, planner, pid = planner_env
    planner.plan(pid, "Build a headless JSON REST API for notes with no frontend.")
    initial_tasks = {t["agent_id"]: t for t in db.list_tasks(pid)}
    assert "frontend_developer" not in initial_tasks

    # Simulate requirement_analyst & software_architect already completed
    db.set_task_status(initial_tasks["requirement_analyst"]["id"], "COMPLETED", result="done")
    db.set_task_status(initial_tasks["software_architect"]["id"], "COMPLETED", result="done")

    # User updates blueprint to request a responsive web dashboard UI
    info = planner.replan(
        pid,
        "Build a full-stack web application with responsive HTML/CSS/JS dashboard UI and REST API for notes.",
    )
    assert "uiux_designer" in info["added_agents"]
    assert "frontend_developer" in info["added_agents"]

    after = {t["agent_id"]: t for t in db.list_tasks(pid)}
    assert after["requirement_analyst"]["status"] == "COMPLETED"
    assert after["software_architect"]["status"] == "COMPLETED"
