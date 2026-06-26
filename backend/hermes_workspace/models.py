from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

MAX_AGENTS = 4


class AgentStatus(str, Enum):
    idle = "idle"
    working = "working"
    paused = "paused"
    error = "error"
    done = "done"


class ProjectStatus(str, Enum):
    draft = "draft"
    planned = "planned"
    running = "running"
    paused = "paused"
    review = "review"
    completed = "completed"
    terminated = "terminated"


class AgentProfile(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    role: str
    cautions: str = ""
    hermes_profile: str | None = None
    is_manager: bool = False
    status: AgentStatus = AgentStatus.idle


class ProjectConfig(BaseModel):
    name: str = "hermes-project"
    goal: str
    profiles: list[AgentProfile]
    workspace_root: str | None = None

    @field_validator("profiles")
    @classmethod
    def validate_profiles(cls, profiles: list[AgentProfile]) -> list[AgentProfile]:
        if not 1 <= len(profiles) <= MAX_AGENTS:
            raise ValueError("profiles must contain 1 to 4 agents")
        managers = [profile for profile in profiles if profile.is_manager]
        if len(managers) != 1:
            raise ValueError("exactly one profile must be marked as Manager")
        return profiles


class AgentEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    type: Literal[
        "thought",
        "tool",
        "message",
        "status",
        "file",
        "human_request",
        "approval_request",
        "system",
    ]
    source: str
    target: str | None = None
    content: str
    metadata: dict = Field(default_factory=dict)


class ProjectState(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    config: ProjectConfig
    status: ProjectStatus = ProjectStatus.draft
    events: list[AgentEvent] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    iteration_count: int = 0
    board_slug: str | None = None
    kanban: dict = Field(default_factory=dict)
    todo_path: str | None = None

    @property
    def manager(self) -> AgentProfile:
        return next(profile for profile in self.config.profiles if profile.is_manager)


def safe_workspace_path(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    root = root.resolve()
    if root not in candidate.parents and candidate != root:
        raise ValueError("path escapes workspace root")
    return candidate
