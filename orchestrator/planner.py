"""Dynamic Blueprint Analyzer & Dependency-Aware DAG Task Planner.

Replaces the fixed 16-step linear pipeline with intelligent Blueprint analysis:
  1. Classifies software category (web_app, rest_api, backend_service, cli_tool,
     python_library, database_app, android_app, desktop_app) and complexity.
  2. Extracts functional/non-functional requirements, domain entities, and tech stack.
  3. Dynamically selects ONLY the Agents needed for the project and excludes irrelevant ones.
  4. Constructs a cycle-checked multi-parent Task DAG with priorities, input/output contracts,
     and explicit acceptance criteria.
  5. Supports runtime replanning (`replan`) when requirements or execution results change.
"""
from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from agents.helpers import extract_json_block, feature_lines, slugify, title_from
from memory.database import Database

# Canonical 16-agent catalog with default priorities and titles
PIPELINE: list[tuple[str, str, int]] = [
    ("requirement_analyst", "Analyze requirements", 10),
    ("software_architect", "Design architecture", 20),
    ("uiux_designer", "Design UI/UX", 30),
    ("database_engineer", "Design database", 35),
    ("backend_developer", "Implement core/backend", 50),
    ("api_integrator", "Integrate external APIs", 60),
    ("frontend_developer", "Implement frontend", 65),
    ("security_engineer", "Security audit", 80),
    ("qa_tester", "Test the build", 85),
    ("debugger", "Diagnose & repair failures", 95),
    ("code_reviewer", "Review & optimize", 105),
    ("devops", "Prepare deployment & scripts", 115),
    ("documentation", "Write technical documentation", 120),
    ("release_manager", "Verify release readiness", 135),
    ("workspace_executor", "Verify workspace state", 140),
    ("chief_orchestrator", "Synthesize orchestration report", 150),
]

QUICK_AGENTS = (
    "requirement_analyst",
    "software_architect",
    "uiux_designer",
    "backend_developer",
    "release_manager",
)

SUPPORTED_CATEGORIES = {
    "web_app",
    "rest_api",
    "backend_service",
    "cli_tool",
    "python_library",
    "database_app",
    "android_app",
    "desktop_app",
}

AGENT_METADATA: dict[str, dict[str, Any]] = {
    "requirement_analyst": {
        "title": "Analyze requirements",
        "priority": 10,
        "outputs": ["docs/requirements.md", "docs/requirements.json"],
        "criteria": ["Structured functional & non-functional requirements extracted"],
    },
    "software_architect": {
        "title": "Design architecture",
        "priority": 20,
        "outputs": ["docs/architecture.md", "ARCHITECTURE.md"],
        "criteria": ["Component architecture, tech stack, and interfaces documented"],
    },
    "uiux_designer": {
        "title": "Design UI/UX",
        "priority": 30,
        "outputs": ["docs/uiux.md"],
        "criteria": ["User flows, screen layout, states, and accessibility defined"],
    },
    "database_engineer": {
        "title": "Design database",
        "priority": 35,
        "outputs": ["database/schema.sql", "database/migrate.py"],
        "criteria": ["Idempotent SQLite schema verified against live SQLite engine"],
    },
    "backend_developer": {
        "title": "Implement core/backend",
        "priority": 50,
        "outputs": ["backend/ or src/ implementation"],
        "criteria": ["Core business logic compiles cleanly and implements blueprint entities"],
    },
    "api_integrator": {
        "title": "Integrate external APIs",
        "priority": 60,
        "outputs": ["backend/api_adapter.py"],
        "criteria": ["HTTP integration client with timeouts, retries, and error typing compiles"],
    },
    "frontend_developer": {
        "title": "Implement frontend",
        "priority": 65,
        "outputs": ["frontend/index.html"],
        "criteria": ["Responsive accessible UI wired to backend endpoints"],
    },
    "security_engineer": {
        "title": "Security audit",
        "priority": 80,
        "outputs": ["docs/security.md"],
        "criteria": ["Zero leaked secrets and vulnerability scan completed"],
    },
    "qa_tester": {
        "title": "Test the build",
        "priority": 85,
        "outputs": ["tests/", "docs/test-report.md"],
        "criteria": ["Automated pytest suite generated and executed with real results"],
    },
    "debugger": {
        "title": "Diagnose & repair failures",
        "priority": 95,
        "outputs": ["docs/debug-report.md"],
        "criteria": ["Failing tests diagnosed, minimal safe fix applied, regression suite re-run"],
    },
    "code_reviewer": {
        "title": "Review & optimize",
        "priority": 105,
        "outputs": ["docs/review.md"],
        "criteria": ["Code quality, maintainability, and Android memory footprint reviewed"],
    },
    "devops": {
        "title": "Prepare deployment & scripts",
        "priority": 115,
        "outputs": ["scripts/run.sh", "docs/deployment.md"],
        "criteria": ["Executable Termux-compatible run script and deployment guide created"],
    },
    "documentation": {
        "title": "Write technical documentation",
        "priority": 120,
        "outputs": ["README.md", "docs/troubleshooting.md"],
        "criteria": ["Accurate README and troubleshooting documentation matching generated files"],
    },
    "release_manager": {
        "title": "Verify release readiness",
        "priority": 135,
        "outputs": ["VERSION", "docs/release-notes.md"],
        "criteria": ["Category-appropriate artifacts and test verdict verified"],
    },
    "workspace_executor": {
        "title": "Verify workspace state",
        "priority": 140,
        "outputs": ["docs/workspace-log.md"],
        "criteria": ["Allowlisted workspace verification command executed safely"],
    },
    "chief_orchestrator": {
        "title": "Synthesize orchestration report",
        "priority": 150,
        "outputs": ["docs/orchestration.md"],
        "criteria": ["Executive orchestration summary and delivery sign-off recorded"],
    },
}


@dataclass
class BlueprintAnalysis:
    project_title: str
    software_type: str                     # one of SUPPORTED_CATEGORIES
    complexity: str                        # "simple" | "moderate" | "complex"
    functional_requirements: list[str]
    non_functional_requirements: list[str]
    entities: list[str]
    technologies: list[str]
    selected_agents: list[str]
    excluded_agents: dict[str, str]        # agent_id -> reason excluded
    needs_database: bool = True
    needs_frontend: bool = False
    needs_uiux: bool = False
    needs_api_integration: bool = False
    platform_limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_title": self.project_title,
            "software_type": self.software_type,
            "complexity": self.complexity,
            "functional_requirements": self.functional_requirements,
            "non_functional_requirements": self.non_functional_requirements,
            "entities": self.entities,
            "technologies": self.technologies,
            "selected_agents": self.selected_agents,
            "excluded_agents": self.excluded_agents,
            "needs_database": self.needs_database,
            "needs_frontend": self.needs_frontend,
            "needs_uiux": self.needs_uiux,
            "needs_api_integration": self.needs_api_integration,
            "platform_limitations": self.platform_limitations,
        }


class BlueprintAnalyzer:
    """Analyzes a blueprint (with optional AI enrichment) to select category, stack, and agents."""

    def analyze(self, requirements: str, *, dynamic: bool = True,
                ai_router=None) -> BlueprintAnalysis:
        raw = (requirements or "").strip() or "Build a small web application."
        low = raw.lower()
        title = title_from(raw, fallback="Generated Project")

        # 1. Software category detection
        software_type = self._detect_category(low)

        # Optional live AI refinement when router is configured
        if ai_router is not None and getattr(ai_router, "mode", lambda: "mock")() == "live":
            ai_hint = self._ai_classify(raw, ai_router)
            if ai_hint.get("software_type") in SUPPORTED_CATEGORIES:
                software_type = ai_hint["software_type"]

        # 2. Extract functional & non-functional requirements + domain entities
        frs, nfrs = self._extract_requirements(raw)
        entities = self._extract_entities(raw, software_type)

        # 3. Complexity estimation
        score = len(frs) + len(entities) + (2 if "auth" in low or "login" in low else 0) + (
            2 if "webhook" in low or "external api" in low or "integration" in low else 0
        )
        complexity = "simple" if score <= 4 else ("moderate" if score <= 9 else "complex")

        # 4. Determine architectural capabilities & Termux limitations
        no_ui_explicit = any(p in low for p in (
            "no frontend", "no ui", "headless", "api only", "backend only", "without frontend",
        ))
        no_db_explicit = any(p in low for p in (
            "stateless", "no database", "in-memory only", "without database",
        ))
        wants_db = not no_db_explicit and (
            software_type in ("web_app", "database_app")
            or any(k in low for k in ("sqlite", "database", "db", "store", "persist", "crud",
                                      "table", "schema", "record", "save", "inventory", "todo",
                                      "user", "book", "order", "note", "habit", "expense", "item"))
            or software_type in ("rest_api", "backend_service", "android_app", "desktop_app")
        )
        if software_type in ("cli_tool", "python_library") and not any(
            k in low for k in ("sqlite", "database", "sql", "persist", "db")
        ):
            wants_db = False

        wants_ext_api = (
            software_type == "web_app"
            or any(k in low for k in (
                "external api", "third-party", "webhook", "http client", "fetch from",
                "integrate with", "integration", "weather", "github api", "stripe", "oauth",
            ))
        )

        wants_frontend = (software_type == "web_app") and not no_ui_explicit
        wants_uiux = (software_type in ("web_app", "android_app", "desktop_app")) and not no_ui_explicit

        technologies, limitations = self._select_technologies(software_type, wants_db, low)

        # 5. Select necessary Agents & document excluded Agents
        if not dynamic:
            selected = [s[0] for s in PIPELINE]
            excluded: dict[str, str] = {}
        else:
            selected, excluded = self._select_agents(
                software_type=software_type,
                complexity=complexity,
                needs_db=wants_db,
                needs_frontend=wants_frontend,
                needs_uiux=wants_uiux,
                needs_api=wants_ext_api,
            )

        return BlueprintAnalysis(
            project_title=title,
            software_type=software_type,
            complexity=complexity,
            functional_requirements=frs,
            non_functional_requirements=nfrs,
            entities=entities,
            technologies=technologies,
            selected_agents=selected,
            excluded_agents=excluded,
            needs_database=wants_db,
            needs_frontend=wants_frontend,
            needs_uiux=wants_uiux,
            needs_api_integration=wants_ext_api,
            platform_limitations=limitations,
        )

    @staticmethod
    def _detect_category(low: str) -> str:
        # Explicit category cues ordered from most specific to broadest
        if any(k in low for k in ("android app", "kotlin app", "apk", "jetpack compose",
                                  "android application", "android studio")):
            return "android_app"
        if any(k in low for k in ("desktop app", "desktop gui", "tkinter", "pyqt", "wxpython",
                                  "desktop application")):
            return "desktop_app"
        if any(k in low for k in ("cli tool", "command-line", "command line", "terminal tool",
                                  "argparse", "cli utility", "cli app")):
            return "cli_tool"
        if any(k in low for k in ("python library", "python package", "reusable library",
                                  "sdk module", "pip package", "utility library")):
            return "python_library"
        if any(k in low for k in ("rest api", "json api", "api server", "microservice",
                                  "api-only", "api only", "headless api")) and not any(
            w in low for w in ("frontend", "spa", "web dashboard", "html page", "ui screen")
        ):
            return "rest_api"
        if any(k in low for k in ("background worker", "backend service", "daemon service",
                                  "queue worker", "webhook processor")) and "frontend" not in low:
            return "backend_service"
        if any(k in low for k in ("database-driven", "etl pipeline", "data warehouse",
                                  "sql ledger", "database reporting")) and "frontend" not in low:
            return "database_app"
        return "web_app"

    @staticmethod
    def _extract_requirements(raw: str) -> tuple[list[str], list[str]]:
        feats = feature_lines(raw, limit=20)
        frs: list[str] = []
        nfrs: list[str] = []
        nfr_keywords = ("fast", "performance", "latency", "memory", "ram", "security",
                        "termux", "offline", "sqlite", "scalable", "timeout", "reliable")
        for f in feats:
            if any(k in f.lower() for k in nfr_keywords) and not any(
                v in f.lower() for v in ("user must", "users can", "add ", "create ", "delete ", "list ")
            ):
                nfrs.append(f)
            else:
                frs.append(f)
        if not frs:
            frs = [raw[:200]]
        if not nfrs:
            nfrs = [
                "Operate within Android/Termux 3 GB RAM constraints with zero heavy daemons.",
                "Validate all external inputs and keep secrets out of source files and logs.",
            ]
        return frs[:15], nfrs[:8]

    @staticmethod
    def _extract_entities(raw: str, software_type: str) -> list[str]:
        candidates = re.findall(
            r"\b(users?|tasks?|todos?|items?|books?|notes?|orders?|products?|expenses?|"
            r"habits?|contacts?|events?|tickets?|invoices?|messages?|projects?|records?)\b",
            raw.lower(),
        )
        seen: list[str] = []
        for c in candidates:
            singular = c[:-1] if c.endswith("s") and c not in ("status",) else c
            plural = singular + "s"
            if plural not in seen:
                seen.append(plural)
        if not seen:
            defaults = {
                "cli_tool": ["commands"],
                "python_library": ["records"],
                "rest_api": ["resources"],
                "android_app": ["entries"],
                "desktop_app": ["notes"],
            }
            seen = defaults.get(software_type, ["items"])
        return seen[:5]

    @staticmethod
    def _select_technologies(software_type: str, needs_db: bool, low: str) -> tuple[list[str], list[str]]:
        limitations: list[str] = []
        if software_type == "android_app":
            tech = ["Kotlin 1.9+", "Gradle Kotlin DSL", "Android SDK API 34", "Python 3 domain/test verifier"]
            if needs_db:
                tech.append("SQLite / Room Schema")
            limitations.append(
                "Native APK compilation (Android SDK/aapt2/Gradle daemon) exceeds standard 3 GB RAM "
                "Termux environments unless Android SDK tools are installed or an approved external "
                "CI build service (e.g. GitHub Actions) is used."
            )
            return tech, limitations
        if software_type == "desktop_app":
            tech = ["Python 3.10+", "tkinter GUI + Headless Presenter", "pytest"]
            if needs_db:
                tech.append("SQLite (WAL)")
            limitations.append(
                "Termux has no native X11/Wayland display server by default; desktop UI requires "
                "Termux:X11 / VNC for visual rendering, while presenter & storage logic run natively."
            )
            return tech, limitations
        if software_type == "cli_tool":
            tech = ["Python 3.10+", "argparse", "json/csv stdlib", "pytest"]
            if needs_db:
                tech.append("SQLite (WAL)")
            return tech, limitations
        if software_type == "python_library":
            tech = ["Python 3.10+", "dataclasses", "typing", "pytest"]
            if needs_db:
                tech.append("SQLite (WAL)")
            return tech, limitations
        if software_type in ("rest_api", "backend_service"):
            tech = ["Python 3.10+", "http.server.ThreadingHTTPServer", "JSON REST", "pytest"]
            if needs_db:
                tech.append("SQLite (WAL)")
            return tech, limitations
        if software_type == "database_app":
            return ["Python 3.10+", "SQLite (WAL)", "Repository & Migration Layer", "pytest"], limitations
        # web_app
        tech = ["Python 3.10+", "ThreadingHTTPServer REST API", "Responsive HTML5/CSS3/Vanilla JS SPA", "pytest"]
        if needs_db:
            tech.append("SQLite (WAL)")
        return tech, limitations

    @staticmethod
    def _select_agents(*, software_type: str, complexity: str, needs_db: bool,
                       needs_frontend: bool, needs_uiux: bool,
                       needs_api: bool) -> tuple[list[str], dict[str, str]]:
        selected = ["requirement_analyst", "software_architect"]
        excluded: dict[str, str] = {}

        if needs_uiux:
            selected.append("uiux_designer")
        else:
            excluded["uiux_designer"] = f"Not required for headless {software_type} project"

        if needs_db:
            selected.append("database_engineer")
        else:
            excluded["database_engineer"] = f"No persistent relational database needed for {software_type}"

        selected.append("backend_developer")

        if needs_api:
            selected.append("api_integrator")
        else:
            excluded["api_integrator"] = "No external third-party HTTP API integration requested"

        if needs_frontend:
            selected.append("frontend_developer")
        else:
            excluded["frontend_developer"] = f"Web SPA frontend not applicable for {software_type}"

        selected.extend(["security_engineer", "qa_tester", "debugger", "code_reviewer"])

        if software_type == "python_library" and complexity == "simple":
            excluded["devops"] = "Simple Python library uses standard package layout without daemon deployment"
        else:
            selected.append("devops")

        selected.extend(["documentation", "release_manager"])

        if software_type == "python_library" and complexity == "simple":
            excluded["workspace_executor"] = "Verified directly by QA & Release Manager for simple library"
        else:
            selected.append("workspace_executor")

        selected.append("chief_orchestrator")
        return selected, excluded

    @staticmethod
    def _ai_classify(raw: str, ai_router) -> dict[str, Any]:
        prompt = (
            "Classify this software blueprint into JSON with keys: "
            '"software_type" (one of: web_app, rest_api, backend_service, cli_tool, '
            'python_library, database_app, android_app, desktop_app) and "complexity" '
            "(simple, moderate, complex).\n\nBlueprint:\n" + raw[:3000]
        )
        try:
            r = ai_router.complete("chief_orchestrator", "Reply with valid JSON only.", prompt, max_tokens=256)
            if r.get("ok") and r.get("mode") == "live":
                return extract_json_block(r.get("text", ""))
        except Exception:
            pass
        return {}


class TaskPlanner:
    """Builds and replans dependency-aware Task DAGs based on BlueprintAnalysis."""

    def __init__(self, db: Database, ai_router=None):
        self.db = db
        self.ai_router = ai_router
        self.analyzer = BlueprintAnalyzer()

    def build_dag_edges(self, selected_agents: list[str]) -> dict[str, list[str]]:
        """Compute multi-parent DAG dependencies among the selected agents."""
        present = set(selected_agents)
        deps: dict[str, list[str]] = {a: [] for a in selected_agents}

        def add_if_present(target: str, candidates: list[str]) -> None:
            if target not in present:
                return
            for c in candidates:
                if c in present and c != target and c not in deps[target]:
                    deps[target].append(c)

        add_if_present("software_architect", ["requirement_analyst"])
        add_if_present("uiux_designer", ["software_architect"])
        add_if_present("database_engineer", ["software_architect"])

        backend_deps = ["database_engineer"] if "database_engineer" in present else ["software_architect"]
        if "software_architect" in present and "software_architect" not in backend_deps:
            backend_deps.insert(0, "software_architect")
        add_if_present("backend_developer", backend_deps)

        add_if_present("api_integrator", ["backend_developer" if "backend_developer" in present else "software_architect"])
        add_if_present("frontend_developer", ["software_architect", "uiux_designer", "backend_developer"])

        impl_agents = [a for a in ("backend_developer", "frontend_developer", "api_integrator", "database_engineer")
                       if a in present]
        if not impl_agents and "software_architect" in present:
            impl_agents = ["software_architect"]

        add_if_present("security_engineer", impl_agents)
        add_if_present("qa_tester", impl_agents)
        add_if_present("debugger", ["qa_tester"] if "qa_tester" in present else impl_agents)

        review_deps = []
        if "debugger" in present:
            review_deps.append("debugger")
        elif "qa_tester" in present:
            review_deps.append("qa_tester")
        if "security_engineer" in present:
            review_deps.append("security_engineer")
        add_if_present("code_reviewer", review_deps or impl_agents)

        post_review = ["code_reviewer"] if "code_reviewer" in present else (review_deps or impl_agents)
        add_if_present("devops", post_review)
        add_if_present("documentation", post_review)

        release_deps = [a for a in ("devops", "documentation", "debugger", "security_engineer", "code_reviewer")
                        if a in present]
        add_if_present("release_manager", release_deps or impl_agents)
        add_if_present("workspace_executor", ["release_manager"] if "release_manager" in present else release_deps)

        if "chief_orchestrator" in present:
            # Chief orchestrator synthesizes after all other selected agents complete
            prior = [a for a in selected_agents if a != "chief_orchestrator"]
            # Depend on leaf nodes of prior graph to keep DAG minimal & complete
            referenced = {p for ch, parents in deps.items() if ch != "chief_orchestrator" for p in parents}
            leaves = [a for a in prior if a not in referenced] or prior[-1:]
            add_if_present("chief_orchestrator", leaves)

        self.validate_dag(deps)
        return deps

    @staticmethod
    def validate_dag(deps: dict[str, list[str]]) -> list[str]:
        """Topological sort (Kahn's algorithm); raises ValueError if a dependency cycle exists."""
        in_degree = {node: 0 for node in deps}
        children: dict[str, list[str]] = {node: [] for node in deps}
        for node, parents in deps.items():
            for p in parents:
                if p not in deps:
                    raise ValueError(f"Task {node!r} depends on unknown node {p!r}")
                in_degree[node] += 1
                children[p].append(node)

        queue = deque(sorted([n for n, deg in in_degree.items() if deg == 0]))
        order: list[str] = []
        while queue:
            cur = queue.popleft()
            order.append(cur)
            for ch in sorted(children[cur]):
                in_degree[ch] -= 1
                if in_degree[ch] == 0:
                    queue.append(ch)
        if len(order) != len(deps):
            raise ValueError("Cycle detected in task dependency DAG")
        return order

    def plan(self, project_id: str, requirements: str = "", quick: bool = False,
             *, dynamic: bool = True) -> list[str]:
        """Analyze the blueprint, select necessary agents, persist analysis, and create the Task DAG."""
        analysis = self.analyzer.analyze(requirements, dynamic=dynamic, ai_router=self.ai_router)
        if quick:
            selected = [a for a in QUICK_AGENTS]
            analysis.selected_agents = selected

        # Store analysis in shared ProjectMemory so every Agent knows software_type, entities, stack
        mem = self.db.get_memory(project_id)
        mem["blueprint_analysis"] = analysis.to_dict()
        self.db.set_memory(project_id, mem)
        self.db.update_project_metadata(project_id, {
            "software_type": analysis.software_type,
            "complexity": analysis.complexity,
            "selected_agents": analysis.selected_agents,
            "excluded_agents": analysis.excluded_agents,
            "technologies": analysis.technologies,
        })

        dag = self.build_dag_edges(analysis.selected_agents)
        task_id_by_agent: dict[str, str] = {}
        ordered_ids: list[str] = []

        # Create tasks in priority/topological order so dependency IDs exist before dependents
        topo = sorted(
            analysis.selected_agents,
            key=lambda a: AGENT_METADATA.get(a, {}).get("priority", 100),
        )
        # Ensure parents always precede children
        created_order = self._topological_by_priority(analysis.selected_agents, dag)

        for agent_id in created_order:
            meta = AGENT_METADATA.get(agent_id, {
                "title": f"Execute {agent_id}", "priority": 100, "outputs": [], "criteria": []
            })
            parent_ids = [task_id_by_agent[p] for p in dag.get(agent_id, []) if p in task_id_by_agent]
            payload = {
                "requirements": (requirements or "")[:8000],
                "software_type": analysis.software_type,
                "complexity": analysis.complexity,
                "entities": analysis.entities,
                "technologies": analysis.technologies,
                "platform_limitations": analysis.platform_limitations,
                "expected_outputs": meta.get("outputs", []),
                "blueprint_analysis": analysis.to_dict(),
            }
            if agent_id == "workspace_executor":
                payload.setdefault("command", "ls -la")
            tid = self.db.create_task(
                project_id=project_id,
                agent_id=agent_id,
                title=meta["title"],
                payload=payload,
                priority=meta["priority"],
                depends_on=parent_ids or None,
                acceptance_criteria=meta.get("criteria", []),
            )
            task_id_by_agent[agent_id] = tid
            ordered_ids.append(tid)

        return ordered_ids

    def replan(self, project_id: str, updated_requirements: str,
               execution_feedback: str = "") -> dict[str, Any]:
        """Re-analyze updated requirements/results and dynamically add or requeue affected DAG nodes."""
        combined = updated_requirements
        if execution_feedback:
            combined = f"{updated_requirements}\n\nExecution Feedback:\n{execution_feedback}"

        analysis = self.analyzer.analyze(combined, dynamic=True, ai_router=self.ai_router)
        mem = self.db.get_memory(project_id)
        prev_analysis = mem.get("blueprint_analysis") or {}
        mem["blueprint_analysis"] = analysis.to_dict()
        self.db.set_memory(project_id, mem)
        self.db.update_project_metadata(project_id, {
            "software_type": analysis.software_type,
            "complexity": analysis.complexity,
            "selected_agents": analysis.selected_agents,
            "excluded_agents": analysis.excluded_agents,
            "technologies": analysis.technologies,
            "replanned": True,
        })

        existing_tasks = {t["agent_id"]: t for t in self.db.list_tasks(project_id)}
        dag = self.build_dag_edges(analysis.selected_agents)
        created_order = self._topological_by_priority(analysis.selected_agents, dag)

        added_agents: list[str] = []
        requeued_agents: list[str] = []
        task_id_by_agent: dict[str, str] = {aid: t["id"] for aid, t in existing_tasks.items()}

        category_changed = prev_analysis.get("software_type") not in (None, analysis.software_type)

        for agent_id in created_order:
            meta = AGENT_METADATA.get(agent_id, {"title": f"Execute {agent_id}", "priority": 100,
                                                 "outputs": [], "criteria": []})
            parent_ids = [task_id_by_agent[p] for p in dag.get(agent_id, []) if p in task_id_by_agent]
            payload = {
                "requirements": combined[:8000],
                "software_type": analysis.software_type,
                "complexity": analysis.complexity,
                "entities": analysis.entities,
                "technologies": analysis.technologies,
                "platform_limitations": analysis.platform_limitations,
                "expected_outputs": meta.get("outputs", []),
                "blueprint_analysis": analysis.to_dict(),
            }
            if agent_id == "workspace_executor":
                payload.setdefault("command", "ls -la")

            if agent_id not in existing_tasks:
                tid = self.db.create_task(
                    project_id=project_id,
                    agent_id=agent_id,
                    title=meta["title"],
                    payload=payload,
                    priority=meta["priority"],
                    depends_on=parent_ids or None,
                    acceptance_criteria=meta.get("criteria", []),
                )
                task_id_by_agent[agent_id] = tid
                added_agents.append(agent_id)
            else:
                t = existing_tasks[agent_id]
                tid = t["id"]
                self.db.update_task_payload(tid, payload)
                for dep in parent_ids:
                    if dep and dep != tid:
                        self.db.execute(
                            "INSERT OR IGNORE INTO task_dependencies(task_id,depends_on) VALUES(?,?)",
                            (tid, dep),
                        )
                # Requeue downstream synthesis/QA/docs if new upstream agents were added or category changed
                should_requeue = (
                    t["status"] in ("FAILED", "SKIPPED")
                    or (t["status"] == "COMPLETED" and (
                        category_changed
                        or any(p in added_agents or p in requeued_agents for p in dag.get(agent_id, []))
                        or (bool(execution_feedback) and agent_id in ("debugger", "qa_tester", "release_manager", "chief_orchestrator"))
                    ))
                )
                if should_requeue and t["status"] != "EXECUTING":
                    self.db.set_task_status(tid, "QUEUED", error="")
                    requeued_agents.append(agent_id)

        return {
            "software_type": analysis.software_type,
            "complexity": analysis.complexity,
            "selected_agents": analysis.selected_agents,
            "added_agents": added_agents,
            "requeued_agents": requeued_agents,
        }

    def plan_custom(self, project_id: str, steps: list[dict]) -> list[str]:
        """Ad-hoc pipeline (Telegram `/run`), still dependency-linked."""
        ids: list[str] = []
        prev: str | None = None
        for i, s in enumerate(steps):
            deps = s.get("depends_on")
            if deps is None:
                deps = [prev] if prev else None
            tid = self.db.create_task(
                project_id,
                s.get("agent_id", "backend_developer"),
                s.get("title", f"Step {i}"),
                payload=s.get("payload", {}),
                priority=s.get("priority", 100 + i),
                depends_on=deps,
                acceptance_criteria=s.get("acceptance_criteria", []),
            )
            ids.append(tid)
            prev = tid
        return ids

    @staticmethod
    def _topological_by_priority(agents: list[str], dag: dict[str, list[str]]) -> list[str]:
        remaining = set(agents)
        ordered: list[str] = []
        while remaining:
            ready = [a for a in remaining if all(p not in remaining for p in dag.get(a, []))]
            if not ready:
                raise ValueError("Cycle detected while ordering DAG")
            ready.sort(key=lambda a: AGENT_METADATA.get(a, {}).get("priority", 100))
            pick = ready[0]
            ordered.append(pick)
            remaining.remove(pick)
        return ordered
