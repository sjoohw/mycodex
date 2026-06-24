import pytest

from backend.hermes_workspace.broker import ConnectionBroker
from backend.hermes_workspace.models import AgentProfile, ProjectConfig, ProjectStatus
from backend.hermes_workspace.orchestrator import HermesOrchestrator


@pytest.mark.anyio
async def test_assign_roles_creates_plan(tmp_path):
    orchestrator = HermesOrchestrator(tmp_path, ConnectionBroker())
    config = ProjectConfig(
        goal="Build a demo",
        profiles=[
            AgentProfile(id="manager", name="Manager", role="Lead", is_manager=True),
            AgentProfile(id="worker", name="Worker", role="Engineer"),
        ],
    )

    state = await orchestrator.configure(config)
    assert state.status == ProjectStatus.draft

    planned = await orchestrator.assign_roles()

    assert planned.status == ProjectStatus.planned
    assert "shared/plan.md" in planned.files
    assert (tmp_path / "shared" / "plan.md").exists()
    assert "Build a demo" in (tmp_path / "shared" / "plan.md").read_text()


@pytest.mark.anyio
async def test_info_message_does_not_require_reply(tmp_path):
    orchestrator = HermesOrchestrator(tmp_path, ConnectionBroker())
    await orchestrator.configure(
        ProjectConfig(
            goal="Build",
            profiles=[AgentProfile(id="m", name="M", role="Manager", is_manager=True)],
        )
    )

    event = await orchestrator.send_message("m", "m", "FYI", "info")

    assert event.metadata["reply_required"] is False
