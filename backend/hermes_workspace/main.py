from __future__ import annotations

import os
import json
import re
import sys
import asyncio
from datetime import datetime, timezone
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .broker import ConnectionBroker
from .models import ProjectConfig
from .orchestrator import HermesOrchestrator

app = FastAPI(title="Hermes Multi-Agent Workspace")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
broker = ConnectionBroker()
workspace_root = Path(
    os.getenv("HERMES_WORKSPACE_ROOT")
    or (Path.cwd() / "workspace" / "project_name")
)
orchestrator = HermesOrchestrator(workspace_root, broker)


class MessageRequest(BaseModel):
    source: str
    target: str
    content: str
    message_type: str = "info"


class FileRequest(BaseModel):
    agent_id: str
    path: str
    content: str | None = None
    command: str | None = None


class TodoLoadRequest(BaseModel):
    config: ProjectConfig
    path: str


class TodoSaveRequest(BaseModel):
    path: str | None = None
    content: str


class HumanRequest(BaseModel):
    manager_id: str
    worker_id: str | None = None
    question: str | None = None
    summary: str | None = None


class ProfileSummary(BaseModel):
    id: str
    name: str
    hermes_profile: str


class DashboardSlot(BaseModel):
    id: str
    profile: ProfileSummary | None = None
    role: str = ""
    cautions: str = ""
    is_manager: bool = False


class DashboardPreset(BaseModel):
    name: str
    goal: str = ""
    workspace_root: str | None = None
    slots: list[DashboardSlot] = Field(default_factory=list)


class DashboardPresetSummary(BaseModel):
    name: str
    updated_at: str | None = None


def request_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def dashboard_store_root() -> Path:
    configured = os.getenv("HERMES_DASHBOARD_ROOT")
    if configured:
        return Path(configured).expanduser()
    return Path.cwd() / "workspace" / "dashboard_presets"


def dashboard_preset_path(name: str) -> Path:
    clean = name.strip()
    if not clean:
        raise ValueError("dashboard preset name is required")
    if len(clean) > 80:
        raise ValueError("dashboard preset name must be 80 characters or shorter")
    if clean in {".", ".."} or Path(clean).name != clean:
        raise ValueError("dashboard preset name must not contain path separators")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]*", clean):
        raise ValueError("dashboard preset name contains invalid characters")
    return dashboard_store_root() / f"{clean}.json"


def profile_search_roots() -> list[Path]:
    configured = os.getenv("HERMES_PROFILE_ROOT")
    if configured:
        return [Path(configured).expanduser()]

    roots: list[Path] = []
    if sys.platform == "win32":
        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data:
            roots.append(Path(local_app_data) / "hermes" / "profiles")
    else:
        xdg_config_home = os.getenv("XDG_CONFIG_HOME")
        if xdg_config_home:
            roots.append(Path(xdg_config_home) / "hermes" / "profiles")
        home = Path.home()
        roots.extend([
            home / ".config" / "hermes" / "profiles",
            home / ".hermes" / "profiles",
            home / ".local" / "share" / "hermes" / "profiles",
        ])
    return roots


def profile_error(message: str, roots: list[Path]) -> HTTPException:
    searched = ", ".join(str(root) for root in roots) or "no profile roots configured"
    return HTTPException(status_code=404, detail=f"{message}. Searched: {searched}")


@app.get("/api/state")
def get_state():
    return orchestrator.current_state()


@app.get("/api/profiles")
def list_profiles() -> list[ProfileSummary]:
    roots = profile_search_roots()
    profiles = []
    existing_roots = [root for root in roots if root.exists() and root.is_dir()]
    if not existing_roots:
        raise profile_error("Hermes profile directory was not found", roots)
    for profile_root in existing_roots:
        for path in sorted(profile_root.iterdir()):
            if path.is_dir() and (path / "config.yaml").exists():
                profiles.append(
                    ProfileSummary(
                        id=path.name,
                        name=path.name.replace("_", " ").replace("-", " ").title(),
                        hermes_profile=path.name,
                    )
                )
    if not profiles:
        raise profile_error("No Hermes profiles with config.yaml were found", existing_roots)
    return profiles


@app.get("/api/dashboard-presets")
def list_dashboard_presets() -> list[DashboardPresetSummary]:
    root = dashboard_store_root()
    if not root.exists():
        return []
    presets = []
    for path in sorted(root.glob("*.json")):
        updated_at = None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            updated_at = data.get("updated_at")
        except (OSError, json.JSONDecodeError):
            pass
        presets.append(DashboardPresetSummary(name=path.stem, updated_at=updated_at))
    return presets


@app.post("/api/dashboard-presets")
def save_dashboard_preset(preset: DashboardPreset) -> DashboardPresetSummary:
    try:
        if len(preset.slots) > 4:
            raise ValueError("dashboard preset can contain at most 4 slots")
        path = dashboard_preset_path(preset.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        updated_at = datetime.now(timezone.utc).isoformat()
        payload = preset.model_dump(mode="json")
        payload["name"] = preset.name.strip()
        payload["updated_at"] = updated_at
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_path.replace(path)
        return DashboardPresetSummary(name=payload["name"], updated_at=updated_at)
    except (ValueError, OSError) as exc:
        raise request_error(exc)


@app.get("/api/dashboard-presets/{name}")
def load_dashboard_preset(name: str) -> DashboardPreset:
    try:
        path = dashboard_preset_path(name)
        if not path.exists():
            raise ValueError("dashboard preset was not found")
        data = json.loads(path.read_text(encoding="utf-8"))
        return DashboardPreset(**data)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise request_error(exc)


@app.post("/api/configure")
async def configure(config: ProjectConfig):
    try:
        return await orchestrator.configure(config)
    except (ValueError, RuntimeError, OSError) as exc:
        raise request_error(exc)


@app.post("/api/new-project")
async def new_project(config: ProjectConfig):
    try:
        return await orchestrator.configure(config)
    except (ValueError, RuntimeError, OSError) as exc:
        raise request_error(exc)


@app.post("/api/generate-todo")
async def generate_todo(config: ProjectConfig):
    try:
        return await orchestrator.generate_todo(config)
    except (ValueError, RuntimeError, OSError) as exc:
        raise request_error(exc)


@app.get("/api/todo")
def get_todo(path: str | None = None):
    state = orchestrator.current_state()
    todo_path = path or (state.todo_path if state else None)
    if not todo_path:
        return {"path": None, "content": ""}
    file_path = orchestrator.workspace_root / todo_path
    try:
        file_path = file_path.resolve()
        workspace = orchestrator.workspace_root.resolve()
        if workspace not in file_path.parents and file_path != workspace:
            raise ValueError("path escapes workspace")
        return {"path": todo_path, "content": file_path.read_text(encoding="utf-8")}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/save-todo")
async def save_todo(request: TodoSaveRequest):
    try:
        return await orchestrator.save_todo(request.path or "", request.content)
    except (ValueError, RuntimeError, OSError) as exc:
        raise request_error(exc)


@app.get("/api/todo-files")
def todo_files(workspace_root: str | None = None) -> list[str]:
    try:
        root = orchestrator.resolve_workspace_root(workspace_root) if workspace_root is not None else orchestrator.workspace_root
    except (ValueError, OSError) as exc:
        raise request_error(exc)
    if root.exists() and not root.is_dir():
        raise HTTPException(status_code=400, detail="workspace_root must be a directory path")
    if not root.exists():
        return []
    ignored_parts = {".hermes", "__pycache__"}
    files = []
    for path in root.rglob("*.md"):
        relative = path.relative_to(root)
        if any(part in ignored_parts for part in relative.parts):
            continue
        files.append(relative.as_posix())
    return sorted(files)


@app.post("/api/load-todo")
async def load_todo(request: TodoLoadRequest):
    try:
        return await orchestrator.load_todo(request.config, request.path)
    except (ValueError, RuntimeError, OSError) as exc:
        raise request_error(exc)


@app.post("/api/assign-roles")
async def assign_roles():
    return await orchestrator.assign_roles()


@app.post("/api/start")
async def start_project():
    return await orchestrator.start()


@app.post("/api/stop")
async def stop_project():
    return await orchestrator.stop()


@app.post("/api/pause")
async def pause_project():
    return await orchestrator.pause()


@app.post("/api/terminate")
async def terminate_project():
    return await orchestrator.terminate()


@app.post("/api/send-message")
async def send_message(request: MessageRequest):
    return await orchestrator.send_message(request.source, request.target, request.content, request.message_type)


@app.post("/api/read-wiki")
async def read_wiki(request: FileRequest):
    return {"content": await orchestrator.read_wiki(request.agent_id, request.path)}


@app.post("/api/write-file")
async def write_file(request: FileRequest):
    return await orchestrator.write_file(request.agent_id, request.path, request.content or "")


@app.post("/api/execute-bash")
async def execute_bash(request: FileRequest):
    return await orchestrator.execute_bash(request.agent_id, request.command or "")


@app.post("/api/ping-worker")
async def ping_worker(request: HumanRequest):
    return await orchestrator.ping_worker(request.manager_id, request.worker_id or "")


@app.post("/api/pause-and-ask-human")
async def pause_and_ask_human(request: HumanRequest):
    return await orchestrator.pause_and_ask_human(request.manager_id, request.question or "Human input required.")


@app.post("/api/request-human-approval")
async def request_human_approval(request: HumanRequest):
    return await orchestrator.request_human_approval(request.manager_id, request.summary or "Please review final result.")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await broker.connect(websocket)
    if orchestrator.state:
        await websocket.send_json({"kind": "state", "state": orchestrator.state.model_dump(mode="json")})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        broker.disconnect(websocket)
