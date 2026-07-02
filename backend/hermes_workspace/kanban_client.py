from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import shutil
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class KanbanCommandResult:
    args: list[str]
    stdout: str
    stderr: str
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def json(self) -> Any:
        return json.loads(self.stdout or "null")


class KanbanClient:
    def __init__(self, executable: str | None = None, timeout_seconds: int = 120) -> None:
        self.executable = executable or self._resolve_executable()
        self.timeout_seconds = timeout_seconds

    async def ensure_board(
        self,
        *,
        slug: str,
        name: str,
        description: str,
        default_workdir: Path,
    ) -> None:
        result = await self.run(
            [
                "boards",
                "create",
                slug,
                "--name",
                name,
                "--description",
                description,
                "--default-workdir",
                str(default_workdir),
            ],
            board=None,
        )
        if not result.ok:
            raise RuntimeError(result.stderr or result.stdout)
        await self.run(["boards", "set-default-workdir", slug, str(default_workdir)], board=None)

    async def create_swarm(
        self,
        *,
        board: str,
        goal: str,
        worker_specs: list[str],
        verifier: str,
        synthesizer: str,
        created_by: str,
        idempotency_key: str,
    ) -> dict:
        args = ["swarm", goal]
        for worker_spec in worker_specs:
            args.extend(["--worker", worker_spec])
        args.extend(
            [
                "--verifier",
                verifier,
                "--synthesizer",
                synthesizer,
                "--created-by",
                created_by,
                "--idempotency-key",
                idempotency_key,
                "--json",
            ]
        )
        result = await self.run(args, board=board, timeout=max(self.timeout_seconds, 240))
        if not result.ok:
            raise RuntimeError(result.stderr or result.stdout)
        return result.json()

    async def dispatch(self, *, board: str, max_spawns: int = 4) -> dict | list | None:
        result = await self.run(["dispatch", "--max", str(max_spawns), "--json"], board=board)
        if not result.ok:
            raise RuntimeError(result.stderr or result.stdout)
        return result.json()

    async def list_tasks(self, *, board: str) -> list[dict]:
        result = await self.run(["list", "--json", "--sort", "created"], board=board)
        if not result.ok:
            raise RuntimeError(result.stderr or result.stdout)
        data = result.json()
        return data if isinstance(data, list) else []

    async def show_task(self, *, board: str, task_id: str) -> dict:
        result = await self.run(["show", task_id, "--json"], board=board)
        if not result.ok:
            raise RuntimeError(result.stderr or result.stdout)
        data = result.json()
        return data if isinstance(data, dict) else {}

    async def task_log(self, *, board: str, task_id: str, tail: int = 6000) -> str:
        result = await self.run(["log", task_id, "--tail", str(tail)], board=board)
        return result.stdout if result.ok else result.stderr

    async def run(
        self,
        kanban_args: list[str],
        *,
        board: str | None,
        timeout: int | None = None,
    ) -> KanbanCommandResult:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["HERMES_ACCEPT_HOOKS"] = "1"
        env["PATH"] = self._path_with_hermes(env.get("PATH", ""))
        if board:
            env["HERMES_KANBAN_BOARD"] = board
        args = [self.executable, "kanban", *kanban_args]
        return await self._run_process(args, env, timeout or self.timeout_seconds)

    async def _run_process(self, args: list[str], env: dict[str, str], timeout: int) -> KanbanCommandResult:
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = 0x00000200  # CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True

        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                **kwargs,
            )
        except FileNotFoundError as exc:
            return KanbanCommandResult(args=args, stdout="", stderr=str(exc), returncode=127)
        except PermissionError as exc:
            return KanbanCommandResult(args=args, stdout="", stderr=str(exc), returncode=126)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await self._terminate_process_tree(process)
            stdout, stderr = await process.communicate()
            return KanbanCommandResult(
                args=args,
                stdout=self._decode_process_output(stdout),
                stderr=(self._decode_process_output(stderr) + f"\nTimed out after {timeout}s.").strip(),
                returncode=124,
            )
        except asyncio.CancelledError:
            await self._terminate_process_tree(process)
            raise
        return KanbanCommandResult(
            args=args,
            stdout=self._decode_process_output(stdout),
            stderr=self._decode_process_output(stderr),
            returncode=process.returncode or 0,
        )

    async def _terminate_process_tree(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        if os.name == "nt":
            with suppress(FileNotFoundError, PermissionError):
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.communicate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)

    def _decode_process_output(self, output: str | bytes | None) -> str:
        if output is None:
            return ""
        if isinstance(output, bytes):
            return output.decode("utf-8", errors="replace").strip()
        return output.strip()

    def _resolve_executable(self) -> str:
        configured = os.getenv("HERMES_EXECUTABLE")
        if configured:
            return configured
        found = shutil.which("hermes")
        if found:
            return found
        for candidate in self._hermes_executable_candidates():
            if candidate.exists():
                return str(candidate)
        return "hermes"

    def _path_with_hermes(self, current_path: str) -> str:
        parts = [str(path) for path in self._extra_path_dirs() if path.exists()]
        parts.append(current_path)
        return os.pathsep.join(part for part in parts if part)

    def _hermes_executable_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data:
            local_root = Path(local_app_data) / "hermes"
            candidates.append(local_root / "hermes-agent" / "venv" / "Scripts" / "hermes.exe")
        home = Path.home()
        candidates.extend([
            home / ".local" / "bin" / "hermes",
            home / ".local" / "share" / "hermes" / "hermes-agent" / "venv" / "bin" / "hermes",
            home / ".config" / "hermes" / "hermes-agent" / "venv" / "bin" / "hermes",
            home / ".hermes" / "hermes-agent" / "venv" / "bin" / "hermes",
        ])
        return candidates

    def _extra_path_dirs(self) -> list[Path]:
        dirs = [path.parent for path in self._hermes_executable_candidates()]
        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data:
            local_root = Path(local_app_data) / "hermes"
            dirs.extend([
                local_root / "git" / "cmd",
                local_root / "git" / "bin",
            ])
        return dirs


def board_slug_for(project_id: str, project_name: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", project_name.lower()).strip("-") or "hermes-project"
    suffix = re.sub(r"[^a-z0-9]+", "", project_id.lower())[:8]
    slug = f"{stem}-{suffix}" if suffix else stem
    return slug[:63].strip("-") or "hermes-project"
