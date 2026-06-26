from __future__ import annotations

import asyncio
import os
import re
import subprocess
from contextlib import suppress
from pathlib import Path

from .broker import ConnectionBroker
from .kanban_client import KanbanClient, board_slug_for
from .models import AgentEvent, AgentStatus, ProjectConfig, ProjectState, ProjectStatus, safe_workspace_path
from .storage import StateStore


class HermesOrchestrator:
    def __init__(self, workspace_root: Path, broker: ConnectionBroker, kanban_client: KanbanClient | None = None):
        self.default_workspace_root = workspace_root.resolve()
        self.workspace_root = self.default_workspace_root
        self.shared_root = self.workspace_root / "shared"
        self.shared_root.mkdir(parents=True, exist_ok=True)
        self.store = StateStore(self.workspace_root / ".hermes")
        self.broker = broker
        self.kanban = kanban_client or KanbanClient()
        self.state = self.store.load()
        self._project_task: asyncio.Task | None = None

    async def configure(self, config: ProjectConfig) -> ProjectState:
        next_root = self.resolve_workspace_root(config.workspace_root)
        if self._project_task and not self._project_task.done():
            raise RuntimeError("project is running. Pause or stop before changing project settings.")
        if self.state and self.state.status == ProjectStatus.running:
            raise RuntimeError("project is running. Pause or stop before changing project settings.")
        self._project_task = None
        self._apply_workspace_root(next_root)
        config.workspace_root = str(self.workspace_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.shared_root.mkdir(parents=True, exist_ok=True)
        for profile in config.profiles:
            profile.status = AgentStatus.idle
        self.state = ProjectState(config=config)
        self.state.board_slug = board_slug_for(self.state.id, config.name)
        self.state.kanban = {"tasks": [], "swarm": {}, "known_statuses": {}}
        await self._record("system", "system", None, f"New project created: {config.name}")
        return self.state

    def resolve_workspace_root(self, workspace_root: str | None) -> Path:
        if workspace_root and workspace_root.strip():
            raw_path = workspace_root.strip().strip('"')
            if not raw_path:
                return self.default_workspace_root
            self._validate_workspace_root_text(raw_path)
            next_root = Path(raw_path).expanduser().resolve()
        else:
            next_root = self.default_workspace_root
        if next_root == Path(next_root.anchor):
            raise ValueError("workspace_root cannot be a filesystem root")
        if next_root.exists() and not next_root.is_dir():
            raise ValueError("workspace_root must be a directory path")
        return next_root

    def _validate_workspace_root_text(self, raw_path: str) -> None:
        if "\x00" in raw_path or any(ord(char) < 32 for char in raw_path):
            raise ValueError("workspace_root contains invalid control characters")
        if os.name != "nt":
            return
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        invalid_chars = set('<>"|?*')
        path = Path(raw_path)
        for part in path.parts:
            if part in {path.anchor, path.drive, "\\", "/"}:
                continue
            if any(char in invalid_chars for char in part) or ":" in part:
                raise ValueError("workspace_root contains characters not allowed on Windows")
            stem = part.split(".")[0].upper()
            if stem in reserved:
                raise ValueError("workspace_root contains a reserved Windows name")

    def _apply_workspace_root(self, next_root: Path) -> None:
        if next_root == self.workspace_root:
            return
        self.workspace_root = next_root
        self.shared_root = self.workspace_root / "shared"
        try:
            self.shared_root.mkdir(parents=True, exist_ok=True)
            (self.workspace_root / ".hermes").mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"cannot create workspace directory: {exc}") from exc
        self.store = StateStore(self.workspace_root / ".hermes")

    async def generate_todo(self, config: ProjectConfig) -> ProjectState:
        state = await self.configure(config)
        manager = state.manager
        todo = self._build_todo_markdown(state)
        await self.write_file(manager.id, "shared/todo.md", todo)
        state.todo_path = "shared/todo.md"
        state.status = ProjectStatus.planned
        await self._record("status", manager.id, None, "Generated shared/todo.md.")
        return state

    async def load_todo(self, config: ProjectConfig, path: str) -> ProjectState:
        state = await self.configure(config)
        file_path = safe_workspace_path(self.workspace_root, path)
        if file_path.suffix.lower() != ".md" or not file_path.exists():
            raise ValueError("selected todo file must be an existing markdown file")
        state.todo_path = path.replace("\\", "/")
        state.status = ProjectStatus.planned
        await self._record("file", state.manager.id, None, f"load_todo({state.todo_path})", {"path": state.todo_path})
        return state

    async def save_todo(self, path: str, content: str) -> ProjectState:
        state = self._require_state()
        todo_path = path or state.todo_path or "shared/todo.md"
        await self.write_file(state.manager.id, todo_path, content)
        state.todo_path = todo_path.replace("\\", "/")
        state.status = ProjectStatus.planned if state.status == ProjectStatus.draft else state.status
        await self._record("status", state.manager.id, None, f"Saved todo list: {state.todo_path}")
        return state

    def current_state(self) -> ProjectState | None:
        if self.state:
            self._sync_workspace_files(self.state)
            self.store.save(self.state)
        return self.state

    async def assign_roles(self) -> ProjectState:
        state = self._require_state()
        await self._ensure_kanban_board(state)
        manager = state.manager
        plan = self._build_plan_markdown(state)
        await self.write_file(manager.id, "shared/plan.md", plan)
        state.todo_path = "shared/plan.md"
        state.status = ProjectStatus.planned
        await self._record(
            "status",
            manager.id,
            None,
            "Manager created shared/plan.md and Kanban board is ready.",
            {"board_slug": state.board_slug},
        )
        return state

    async def start(self) -> ProjectState:
        state = self._require_state()
        if state.status == ProjectStatus.terminated:
            await self._record("status", "system", None, "Project is terminated. Create a new project first.")
            return state
        if self._project_task and not self._project_task.done():
            await self._record("status", "system", None, "Project is already running.")
            return state
        state.status = ProjectStatus.running
        for profile in state.config.profiles:
            profile.status = AgentStatus.working if profile.is_manager else AgentStatus.idle
        await self._ensure_kanban_board(state)
        if not state.todo_path:
            await self.save_todo("shared/todo.md", self._build_todo_markdown(state))
        swarm = await self._ensure_swarm(state)
        await self._record(
            "status",
            state.manager.id,
            None,
            "Project Start: Kanban swarm created and dispatcher loop started.",
            {"board_slug": state.board_slug, "swarm": swarm},
        )
        self._project_task = asyncio.create_task(self._dispatch_and_monitor())
        return state

    async def pause(self) -> ProjectState:
        state = self._require_state()
        if self._project_task and not self._project_task.done():
            self._project_task.cancel()
        state.status = ProjectStatus.paused
        for profile in state.config.profiles:
            profile.status = AgentStatus.paused
        await self._record("status", "system", None, "Project paused by user.")
        return state

    async def stop(self) -> ProjectState:
        return await self.pause()

    async def terminate(self) -> ProjectState:
        state = self._require_state()
        if self._project_task and not self._project_task.done():
            self._project_task.cancel()
        state.status = ProjectStatus.terminated
        for profile in state.config.profiles:
            profile.status = AgentStatus.idle
        state.kanban["terminated"] = True
        await self._record("status", "system", None, "Project terminated by user.")
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

    async def _dispatch_and_monitor(self) -> None:
        state = self._require_state()
        try:
            while True:
                state.iteration_count += 1
                dispatch_result = await self.kanban.dispatch(board=state.board_slug or "default")
                state.kanban["last_dispatch"] = dispatch_result
                self._sync_workspace_files(state)
                tasks = await self._sync_kanban_state(state)
                blocked_tasks = [task for task in tasks if task.get("status") == "blocked"]
                if blocked_tasks:
                    state.status = ProjectStatus.paused
                    log_snippets = await self._blocked_task_logs(state, blocked_tasks)
                    for profile in state.config.profiles:
                        profile_tasks = [
                            task for task in blocked_tasks
                            if task.get("assignee") == self._hermes_profile(profile)
                        ]
                        if profile_tasks:
                            profile.status = AgentStatus.error
                    await self._record(
                        "human_request",
                        state.manager.id,
                        None,
                        self._blocked_summary(blocked_tasks, log_snippets),
                        {"board_slug": state.board_slug, "blocked_tasks": blocked_tasks, "logs": log_snippets},
                    )
                    return
                terminal = self._kanban_terminal(tasks)
                if terminal:
                    if any(task.get("status") == "blocked" for task in tasks):
                        state.status = ProjectStatus.paused
                        await self._record(
                            "human_request",
                            state.manager.id,
                            None,
                            "One or more Kanban tasks are blocked. Review the task logs and provide guidance.",
                            {"board_slug": state.board_slug},
                        )
                    else:
                        state.status = ProjectStatus.review
                        state.manager.status = AgentStatus.done
                        await self._record(
                            "approval_request",
                            state.manager.id,
                            None,
                            "Kanban swarm reached a terminal state. Review the board, logs, and workspace files.",
                            {"board_slug": state.board_slug, "files": state.files},
                        )
                    return
                await asyncio.sleep(15)
        except asyncio.CancelledError:
            await self._record("status", "system", None, "Project run cancelled.")
            raise
        except Exception as exc:
            state.status = ProjectStatus.paused
            for profile in state.config.profiles:
                if profile.status == AgentStatus.working:
                    profile.status = AgentStatus.error
            await self._record("system", "system", None, f"Project run failed: {exc}")

    async def _ensure_kanban_board(self, state: ProjectState) -> None:
        if not state.board_slug:
            state.board_slug = board_slug_for(state.id, state.config.name)
        await self.kanban.ensure_board(
            slug=state.board_slug,
            name=state.config.name,
            description=state.config.goal[:500],
            default_workdir=self.workspace_root,
        )

    async def _ensure_swarm(self, state: ProjectState) -> dict:
        existing = state.kanban.get("swarm")
        if existing and existing.get("root_id"):
            return existing

        manager = state.manager
        workers = [profile for profile in state.config.profiles if not profile.is_manager]
        if not workers:
            workers = [manager]
        verifier = self._select_verifier(workers)
        executable_workers = [profile for profile in workers if profile.id != verifier.id] or workers
        worker_specs = [
            f"{self._hermes_profile(profile)}:{self._worker_title(profile)}. Produce the requested deliverables, validate them, then mark this task complete. Do not block for routine QA or final review; the verifier task handles review."
            for profile in executable_workers
        ]
        swarm = await self.kanban.create_swarm(
            board=state.board_slug or "default",
            goal=self._kanban_goal(state),
            worker_specs=worker_specs,
            verifier=self._hermes_profile(verifier),
            synthesizer=self._hermes_profile(manager),
            created_by=self._hermes_profile(manager),
            idempotency_key=f"hermes-workspace:{state.id}:swarm",
        )
        state.kanban["swarm"] = swarm
        return swarm

    async def _sync_kanban_state(self, state: ProjectState) -> list[dict]:
        tasks = await self.kanban.list_tasks(board=state.board_slug or "default")
        state.kanban["tasks"] = tasks
        known_statuses = state.kanban.setdefault("known_statuses", {})
        for profile in state.config.profiles:
            profile_tasks = [
                task for task in tasks
                if task.get("assignee") == self._hermes_profile(profile)
            ]
            profile.status = self._profile_status_from_tasks(profile_tasks, profile.status)
        for task in tasks:
            task_id = str(task.get("id") or "")
            status = str(task.get("status") or "")
            previous = known_statuses.get(task_id)
            if task_id and status and previous != status:
                known_statuses[task_id] = status
                await self._record(
                    "status",
                    str(task.get("assignee") or "kanban"),
                    None,
                    f"Kanban task {task_id} is {status}: {task.get('title', '')}",
                    {"task": task, "board_slug": state.board_slug},
                )
        return tasks

    def _sync_workspace_files(self, state: ProjectState) -> None:
        if not self.workspace_root.exists():
            return
        ignored_parts = {".hermes", "__pycache__"}
        files = set(state.files)
        for path in self.workspace_root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.workspace_root)
            if any(part in ignored_parts for part in relative.parts):
                continue
            files.add(relative.as_posix())
        state.files = sorted(files)

    def _profile_status_from_tasks(self, tasks: list[dict], current: AgentStatus) -> AgentStatus:
        statuses = {str(task.get("status") or "") for task in tasks}
        if "running" in statuses:
            return AgentStatus.working
        if "blocked" in statuses:
            return AgentStatus.error
        if "ready" in statuses or "todo" in statuses or "review" in statuses:
            return AgentStatus.idle if current != AgentStatus.working else AgentStatus.working
        if statuses and statuses <= {"done", "archived"}:
            return AgentStatus.done
        return current

    def _kanban_terminal(self, tasks: list[dict]) -> bool:
        if not tasks:
            return False
        active = {"ready", "running", "todo", "triage", "scheduled", "review"}
        return not any(str(task.get("status") or "") in active for task in tasks)

    async def _blocked_task_logs(self, state: ProjectState, blocked_tasks: list[dict]) -> dict[str, str]:
        logs: dict[str, str] = {}
        for task in blocked_tasks:
            task_id = str(task.get("id") or "")
            if not task_id:
                continue
            try:
                logs[task_id] = await self.kanban.task_log(board=state.board_slug or "default", task_id=task_id, tail=2000)
            except Exception as exc:
                logs[task_id] = f"Could not read task log: {exc}"
        return logs

    def _blocked_summary(self, blocked_tasks: list[dict], logs: dict[str, str]) -> str:
        lines = ["One or more Kanban tasks are blocked."]
        for task in blocked_tasks:
            task_id = str(task.get("id") or "")
            title = str(task.get("title") or "Untitled task")
            lines.append(f"- {task_id}: {title}")
            snippet = self._strip_ansi(logs.get(task_id, "")).strip()
            if snippet:
                lines.append(snippet[-1200:])
        return "\n".join(lines)

    def _strip_ansi(self, text: str) -> str:
        return re.sub(r"\x1b\[[0-9;]*m", "", text)

    def _select_verifier(self, workers: list) -> object:
        for worker in workers:
            text = f"{worker.id} {worker.name} {worker.role} {worker.hermes_profile or ''}".lower()
            if "qa" in text or "review" in text or "verify" in text:
                return worker
        return workers[-1]

    def _worker_title(self, profile) -> str:
        role = profile.role.replace(":", "-").strip()
        return f"{profile.name} work - {role}"[:180]

    def _kanban_goal(self, state: ProjectState) -> str:
        return f"""Hermes Multi-Agent Project Management System run.

Workspace root:
{self.workspace_root}

Project goal:
{state.config.goal}

Shared files:
- Read {state.todo_path or "shared/todo.md"} before working.
- Write durable notes, implementation artifacts, and final summaries under the workspace root.

Operating rules:
- Preserve agent autonomy: use Hermes tools directly for file edits, tests, inspection, and implementation.
- Use concise task handoffs. Do not reply to info-only messages unless a response is explicitly required.
- Worker tasks must complete after producing and validating their deliverables. Do not block merely because QA or final review is pending.
- Block only for a genuine unrecoverable issue that needs exact human guidance.
- Synthesizer must produce a final human-review summary.

Profiles:
{self._profiles_markdown(state)}
"""

    def _build_plan_markdown(self, state: ProjectState) -> str:
        return self._build_todo_markdown(state).replace("# Project To-do List", "# Hermes Project Plan", 1)

    def _build_todo_markdown(self, state: ProjectState) -> str:
        workers = [p for p in state.config.profiles if not p.is_manager]
        assignments = []
        assignments.append(f"- [ ] 1. {state.manager.name}: confirm scope, split work, and keep this list updated.")
        for index, worker in enumerate(workers, start=2):
            caution = f" Caution: {worker.cautions}" if worker.cautions else ""
            assignments.append(f"- [ ] {index}. {worker.name}: {worker.role}.{caution}")
        assignments.append(f"- [ ] {len(assignments) + 1}. {state.manager.name}: synthesize results and request final review.")
        profiles = "\n".join(
            f"- {profile.name} ({'Manager' if profile.is_manager else 'Worker'}, Hermes profile: {self._hermes_profile(profile)}): {profile.role}"
            for profile in state.config.profiles
        )
        return f"""# Project To-do List\n\n## Goal\n{state.config.goal}\n\n## Assigned Profiles\n{profiles}\n\n## Sequence\n{chr(10).join(assignments)}\n\n## Operating Rules\n- Read this to-do list before working.\n- Complete steps in order unless the manager updates this file.\n- Do not reply to `type: info` messages unless explicitly requested.\n- Pause and ask a human after unresolved blockers or repeated failures.\n"""

    def _profiles_markdown(self, state: ProjectState) -> str:
        lines = []
        for profile in state.config.profiles:
            marker = "Manager" if profile.is_manager else "Worker"
            details = []
            if getattr(profile, "cautions", ""):
                details.append(f"Cautions: {profile.cautions}")
            suffix = f" {' '.join(details)}" if details else ""
            lines.append(
                f"- {profile.name} ({marker}, app id: {profile.id}, Hermes profile: {self._hermes_profile(profile)}): {profile.role}.{suffix}"
            )
        return "\n".join(lines)

    def _hermes_profile(self, profile) -> str:
        return profile.hermes_profile or profile.id

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
