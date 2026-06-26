import pytest

from backend.hermes_workspace.broker import ConnectionBroker
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
