from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import ProjectState


class StateStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "hermes.db"
        self.snapshot_path = self.root / "state.snapshot.json"
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS project_snapshots (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def save(self, state: ProjectState) -> None:
        payload = state.model_dump_json(indent=2)
        self.snapshot_path.write_text(payload, encoding="utf-8")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO project_snapshots(id, status, payload, updated_at)
                VALUES(?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    payload=excluded.payload,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (state.id, state.status.value, payload),
            )

    def load(self) -> ProjectState | None:
        if not self.snapshot_path.exists():
            return None
        return ProjectState.model_validate(json.loads(self.snapshot_path.read_text(encoding="utf-8")))
