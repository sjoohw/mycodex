from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .broker import ConnectionBroker
from .models import ProjectConfig
from .orchestrator import HermesOrchestrator

app = FastAPI(title="Hermes Multi-Agent Workspace")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
broker = ConnectionBroker()
orchestrator = HermesOrchestrator(Path("/workspace/project_name"), broker)


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


class HumanRequest(BaseModel):
    manager_id: str
    worker_id: str | None = None
    question: str | None = None
    summary: str | None = None


@app.get("/api/state")
def get_state():
    return orchestrator.state


@app.post("/api/configure")
async def configure(config: ProjectConfig):
    return await orchestrator.configure(config)


@app.post("/api/assign-roles")
async def assign_roles():
    return await orchestrator.assign_roles()


@app.post("/api/start")
async def start_project():
    return await orchestrator.start()


@app.post("/api/stop")
async def stop_project():
    return await orchestrator.stop()


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
