import pytest
from fastapi import HTTPException

from backend.hermes_workspace import main as app_main
from backend.hermes_workspace.broker import ConnectionBroker
from backend.hermes_workspace.kanban_client import KanbanClient
from backend.hermes_workspace import kanban_client as kanban_module
from backend.hermes_workspace.models import AgentProfile, ProjectConfig, ProjectStatus
from backend.hermes_workspace.orchestrator import HermesOrchestrator


class FakeKanbanClient:
    def __init__(self):
        self.board = None
        self.swarm = None
        self.tasks = []
        self.dispatch_count = 0

    async def ensure_board(self, *, slug, name, description, default_workdir):
        self.board = {
            "slug": slug,
            "name": name,
            "description": description,
            "default_workdir": str(default_workdir),
        }

    async def create_swarm(self, *, board, goal, worker_specs, verifier, synthesizer, created_by, idempotency_key):
        self.swarm = {
            "root_id": "root",
            "worker_ids": ["worker-task"],
            "verifier_id": "verifier-task",
            "synthesizer_id": "synth-task",
            "board": board,
            "goal": goal,
            "worker_specs": worker_specs,
            "verifier": verifier,
            "synthesizer": synthesizer,
            "created_by": created_by,
            "idempotency_key": idempotency_key,
        }
        self.tasks = [
            {"id": "root", "title": "Swarm root", "status": "done", "assignee": created_by},
            {"id": "worker-task", "title": "Designer work", "status": "ready", "assignee": "layout_designer"},
            {"id": "verifier-task", "title": "Verify", "status": "todo", "assignee": "layout_qa"},
            {"id": "synth-task", "title": "Synthesize", "status": "todo", "assignee": "layout_architect"},
        ]
        return self.swarm

    async def dispatch(self, *, board, max_spawns=4):
        self.dispatch_count += 1
        for task in self.tasks:
            if task["status"] in {"ready", "todo", "running"}:
                task["status"] = "done"
        return {"spawned": []}

    async def list_tasks(self, *, board):
        return self.tasks

    async def task_log(self, *, board, task_id, tail=6000):
        return f"log for {task_id}"


@pytest.mark.anyio
async def test_assign_roles_creates_plan(tmp_path):
    fake_kanban = FakeKanbanClient()
    orchestrator = HermesOrchestrator(tmp_path, ConnectionBroker(), kanban_client=fake_kanban)
    config = ProjectConfig(
        goal="Build a demo",
        profiles=[
            AgentProfile(id="layout_architect", name="Manager", role="Lead", hermes_profile="layout_architect", is_manager=True),
            AgentProfile(id="layout_designer", name="Worker", role="Engineer", hermes_profile="layout_designer"),
        ],
    )

    state = await orchestrator.configure(config)
    assert state.status == ProjectStatus.draft

    planned = await orchestrator.assign_roles()

    assert planned.status == ProjectStatus.planned
    assert "shared/plan.md" in planned.files
    assert (tmp_path / "shared" / "plan.md").exists()
    assert "Build a demo" in (tmp_path / "shared" / "plan.md").read_text()
    assert fake_kanban.board["default_workdir"] == str(tmp_path)


@pytest.mark.anyio
async def test_info_message_does_not_require_reply(tmp_path):
    orchestrator = HermesOrchestrator(tmp_path, ConnectionBroker(), kanban_client=FakeKanbanClient())
    await orchestrator.configure(
        ProjectConfig(
            goal="Build",
            profiles=[AgentProfile(id="m", name="M", role="Manager", is_manager=True)],
        )
    )

    event = await orchestrator.send_message("m", "m", "FYI", "info")

    assert event.metadata["reply_required"] is False


@pytest.mark.anyio
async def test_config_workspace_root_controls_write_location(tmp_path):
    custom_root = tmp_path / "custom-workspace"
    orchestrator = HermesOrchestrator(tmp_path / "default", ConnectionBroker(), kanban_client=FakeKanbanClient())
    await orchestrator.configure(
        ProjectConfig(
            goal="Build in a selected path",
            workspace_root=str(custom_root),
            profiles=[AgentProfile(id="m", name="M", role="Manager", is_manager=True)],
        )
    )

    await orchestrator.write_file("m", "shared/note.md", "hello")

    assert orchestrator.workspace_root == custom_root.resolve()
    assert (custom_root / "shared" / "note.md").read_text() == "hello"


@pytest.mark.anyio
async def test_blank_workspace_root_returns_to_default(tmp_path):
    default_root = tmp_path / "default"
    custom_root = tmp_path / "custom"
    orchestrator = HermesOrchestrator(default_root, ConnectionBroker(), kanban_client=FakeKanbanClient())

    await orchestrator.configure(
        ProjectConfig(
            goal="Custom path",
            workspace_root=str(custom_root),
            profiles=[AgentProfile(id="m", name="M", role="Manager", is_manager=True)],
        )
    )
    await orchestrator.configure(
        ProjectConfig(
            goal="Default path",
            profiles=[AgentProfile(id="m", name="M", role="Manager", is_manager=True)],
        )
    )

    assert orchestrator.workspace_root == default_root.resolve()


def test_workspace_root_rejects_existing_file(tmp_path):
    file_path = tmp_path / "not-a-directory.txt"
    file_path.write_text("content")
    orchestrator = HermesOrchestrator(tmp_path / "default", ConnectionBroker(), kanban_client=FakeKanbanClient())

    with pytest.raises(ValueError, match="directory path"):
        orchestrator.resolve_workspace_root(str(file_path))


def test_workspace_root_rejects_filesystem_root(tmp_path):
    orchestrator = HermesOrchestrator(tmp_path / "default", ConnectionBroker(), kanban_client=FakeKanbanClient())

    with pytest.raises(ValueError, match="filesystem root"):
        orchestrator.resolve_workspace_root(str(tmp_path.anchor))


def test_workspace_root_rejects_control_characters(tmp_path):
    orchestrator = HermesOrchestrator(tmp_path / "default", ConnectionBroker(), kanban_client=FakeKanbanClient())

    with pytest.raises(ValueError, match="control characters"):
        orchestrator.resolve_workspace_root(f"{tmp_path}\ninvalid")


def test_profiles_are_loaded_from_configured_root(tmp_path, monkeypatch):
    profile_root = tmp_path / "profiles"
    profile_dir = profile_root / "linux_profile"
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text("model: test\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_PROFILE_ROOT", str(profile_root))

    profiles = app_main.list_profiles()

    assert [profile.hermes_profile for profile in profiles] == ["linux_profile"]


def test_linux_profile_search_roots_include_xdg_config(tmp_path, monkeypatch):
    xdg_root = tmp_path / "xdg"
    monkeypatch.delenv("HERMES_PROFILE_ROOT", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_root))
    monkeypatch.setattr(app_main.sys, "platform", "linux")

    roots = app_main.profile_search_roots()

    assert roots[0] == xdg_root / "hermes" / "profiles"
    assert any(root.as_posix().endswith(".config/hermes/profiles") for root in roots)


def test_profiles_error_when_root_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE_ROOT", str(tmp_path / "missing"))

    with pytest.raises(HTTPException, match="profile directory"):
        app_main.list_profiles()


def test_dashboard_preset_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_ROOT", str(tmp_path / "dashboards"))
    preset = app_main.DashboardPreset(
        name="teg-demo",
        goal="Build TEG layout",
        workspace_root=str(tmp_path / "workspace"),
        slots=[
            app_main.DashboardSlot(
                id="slot_1",
                profile=app_main.ProfileSummary(id="layout", name="Layout", hermes_profile="layout"),
                role="Implement layout",
                cautions="Verify pitch",
                is_manager=True,
            )
        ],
    )

    saved = app_main.save_dashboard_preset(preset)
    loaded = app_main.load_dashboard_preset("teg-demo")
    summaries = app_main.list_dashboard_presets()

    assert saved.name == "teg-demo"
    assert loaded.goal == "Build TEG layout"
    assert loaded.slots[0].profile.hermes_profile == "layout"
    assert summaries[0].name == "teg-demo"


def test_dashboard_preset_rejects_path_like_name(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_ROOT", str(tmp_path / "dashboards"))

    with pytest.raises(HTTPException, match="path separators"):
        app_main.save_dashboard_preset(app_main.DashboardPreset(name="../bad"))


def test_kanban_client_resolves_linux_home_candidate(tmp_path, monkeypatch):
    executable = tmp_path / ".local" / "bin" / "hermes"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/usr/bin/env sh\n", encoding="utf-8")
    monkeypatch.delenv("HERMES_EXECUTABLE", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(kanban_module.shutil, "which", lambda _: None)
    monkeypatch.setattr(kanban_module.Path, "home", classmethod(lambda cls: tmp_path))

    client = KanbanClient()

    assert client.executable == str(executable)


@pytest.mark.anyio
async def test_configure_is_blocked_while_running(tmp_path):
    orchestrator = HermesOrchestrator(tmp_path, ConnectionBroker(), kanban_client=FakeKanbanClient())
    await orchestrator.configure(
        ProjectConfig(
            goal="Build",
            profiles=[AgentProfile(id="m", name="M", role="Manager", is_manager=True)],
        )
    )
    orchestrator.state.status = ProjectStatus.running

    with pytest.raises(RuntimeError, match="project is running"):
        await orchestrator.configure(
            ProjectConfig(
                goal="Change",
                workspace_root=str(tmp_path / "other"),
                profiles=[AgentProfile(id="m", name="M", role="Manager", is_manager=True)],
            )
        )


@pytest.mark.anyio
async def test_start_creates_kanban_swarm(tmp_path):
    fake_kanban = FakeKanbanClient()
    orchestrator = HermesOrchestrator(tmp_path, ConnectionBroker(), kanban_client=fake_kanban)
    await orchestrator.configure(
        ProjectConfig(
            goal="Ship the UI",
            profiles=[
                AgentProfile(id="layout_architect", name="Architect", role="Plan and synthesize", hermes_profile="layout_architect", is_manager=True),
                AgentProfile(id="layout_designer", name="Designer", role="Build layout", hermes_profile="layout_designer"),
                AgentProfile(id="layout_qa", name="QA", role="Verify layout QA", hermes_profile="layout_qa"),
            ],
        )
    )

    state = await orchestrator.start()

    assert state.status == ProjectStatus.running
    assert fake_kanban.swarm["worker_specs"] == [
        "layout_designer:Designer work - Build layout. Produce the requested deliverables, validate them, then mark this task complete. Do not block for routine QA or final review; the verifier task handles review."
    ]
    assert fake_kanban.swarm["verifier"] == "layout_qa"
    assert fake_kanban.swarm["synthesizer"] == "layout_architect"
