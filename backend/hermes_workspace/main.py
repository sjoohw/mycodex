from __future__ import annotations

import os
import sys
import asyncio
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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


def request_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@app.get("/api/state")
def get_state():
    return orchestrator.current_state()


@app.get("/api/profiles")
def list_profiles() -> list[ProfileSummary]:
    profile_root = Path(os.getenv("HERMES_PROFILE_ROOT") or Path(os.getenv("LOCALAPPDATA", "")) / "hermes" / "profiles")
    if not profile_root.exists():
        return []
    profiles = []
    for path in sorted(profile_root.iterdir()):
        if path.is_dir() and (path / "config.yaml").exists():
            profiles.append(
                ProfileSummary(
                    id=path.name,
                    name=path.name.replace("_", " ").replace("-", " ").title(),
                    hermes_profile=path.name,
                )
            )
    return profiles


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
