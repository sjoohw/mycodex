from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from .broker import ConnectionBroker
from .models import AgentEvent, AgentStatus, ProjectConfig, ProjectState, ProjectStatus, safe_workspace_path
from .storage import StateStore


class HermesOrchestrator:
    def __init__(self, workspace_root: Path, broker: ConnectionBroker):
        self.workspace_root = workspace_root
        self.shared_root = workspace_root / "shared"
        self.shared_root.mkdir(parents=True, exist_ok=True)
        self.store = StateStore(workspace_root / ".hermes")
        self.broker = broker
        self.state = self.store.load()

    async def configure(self, config: ProjectConfig) -> ProjectState:
        self.state = ProjectState(config=config)
        await self._record("system", "system", None, f"Project configured: {config.name}")
        return self.state

    async def assign_roles(self) -> ProjectState:
        state = self._require_state()
        manager = state.manager
        plan = self._build_plan_markdown(state)
        await self.write_file(manager.id, "shared/plan.md", plan)
        state.status = ProjectStatus.planned
        await self._record("status", manager.id, None, "Manager created plan.md and assigned work.")
        return state

    async def start(self) -> ProjectState:
        state = self._require_state()
        state.status = ProjectStatus.running
        for profile in state.config.profiles:
            profile.status = AgentStatus.working if profile.is_manager else AgentStatus.idle
        await self._record("status", state.manager.id, None, "Project Start: manager dispatching first task.")
        first_worker = next((p for p in state.config.profiles if not p.is_manager), None)
        if first_worker:
            await self.send_message(state.manager.id, first_worker.id, "Review shared/plan.md and begin your first assigned task.", "instruction")
        return state

    async def stop(self) -> ProjectState:
        state = self._require_state()
        state.status = ProjectStatus.paused
        for profile in state.config.profiles:
            profile.status = AgentStatus.paused
        await self._record("status", "system", None, "Project paused by user.")
        return state

    async def send_message(self, source: str, target: str, content: str, message_type: str = "info") -> AgentEvent:
        metadata = {"message_type": message_type, "reply_required": message_type != "info"}
        return await self._record("message", source, target, content, metadata)

    async def read_wiki(self, agent_id: str, path: str) -> str:
        file_path = safe_workspace_path(self.workspace_root, path)
        content = file_path.read_text(encoding="utf-8")
        await self._record("tool", agent_id, None, f"read_wiki({path})")
        return content

    async def write_file(self, agent_id: str, path: str, content: str) -> AgentEvent:
        file_path = safe_workspace_path(self.workspace_root, path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        state = self._require_state()
        if path not in state.files:
            state.files.append(path)
        return await self._record("file", agent_id, None, f"write_file({path})", {"path": path})

    async def execute_bash(self, agent_id: str, command: str) -> AgentEvent:
        result = subprocess.run(command, cwd=self.workspace_root, shell=True, text=True, capture_output=True, timeout=60)
        content = f"execute_bash({command}) exited {result.returncode}\n{result.stdout}{result.stderr}"
        return await self._record("tool", agent_id, None, content, {"returncode": result.returncode})

    async def ping_worker(self, manager_id: str, worker_id: str) -> AgentEvent:
        return await self._record("message", manager_id, worker_id, "Please report progress, blockers, and ETA.", {"message_type": "ping"})

    async def pause_and_ask_human(self, manager_id: str, question: str) -> AgentEvent:
        state = self._require_state()
        state.status = ProjectStatus.paused
        return await self._record("human_request", manager_id, None, question)

    async def request_human_approval(self, manager_id: str, summary: str) -> AgentEvent:
        state = self._require_state()
        state.status = ProjectStatus.review
        return await self._record("approval_request", manager_id, None, summary, {"files": state.files})

    def _build_plan_markdown(self, state: ProjectState) -> str:
        workers = [p for p in state.config.profiles if not p.is_manager]
        assignments = "\n".join(f"- [ ] {w.name} ({w.role}): Execute delegated tasks and report blockers." for w in workers)
        return f"""# Hermes Project Plan\n\n## Goal\n{state.config.goal}\n\n## Manager\n- {state.manager.name}: {state.manager.role}\n\n## Assignments\n{assignments or '- [ ] Manager handles all tasks.'}\n\n## Operating Rules\n- Use summarized instructions and point workers to this plan or wiki files.\n- Do not reply to `type: info` messages unless explicitly requested.\n- Pause and ask a human after 10 unresolved iterations or unrecoverable errors.\n"""

    async def _record(self, event_type: str, source: str, target: str | None, content: str, metadata: dict | None = None) -> AgentEvent:
        state = self._require_state() if self.state else None
        event = AgentEvent(type=event_type, source=source, target=target, content=content, metadata=metadata or {})
        if state:
            state.events.append(event)
            self.store.save(state)
        await self.broker.broadcast({"kind": "event", "event": event.model_dump(mode="json"), "state": state.model_dump(mode="json") if state else None})
        return event

    def _require_state(self) -> ProjectState:
        if self.state is None:
            raise RuntimeError("project is not configured")
        return self.state
