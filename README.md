# Hermes Multi-Agent Project Management System

A lightweight prototype for a Hermes-based, maximum-four-agent project workspace.

## Features

- FastAPI backend with a single WebSocket event broker.
- SQLite plus JSON snapshot persistence under `/workspace/project_name/.hermes`.
- Tool endpoints for message passing, wiki reads/writes, bash execution, worker pings, human pause, and approval requests.
- Premium React frontend featuring a glassmorphic dark theme and a 2x2 grid layout for profile assignment.
- Hermes profiles are discovered from the local Hermes profile directory through `/api/profiles`.
- Profiles can be assigned directly to slots using each slot's `Select` button.
- Each assigned slot supports manager selection plus role and cautions fields.
- To-do lists can be generated, loaded from markdown files, edited, and saved in-app.
- Agent logs open in popup views. The monitoring panel focuses on current to-do progress, generated files, and Kanban tasks.
- The workspace path can be set from the frontend before creating or loading a project.
- Dashboard inputs can be saved and loaded as backend-side presets.

## Run

```bash
pip install -e '.[dev]'
npm install
npm run backend
npm run frontend
```

Open the Vite URL and use the control panel to create a project, generate or load a to-do list, and run/pause/stop execution. Vite proxies `/api` and `/ws` to the FastAPI backend at `http://127.0.0.1:8000` by default; set `HERMES_BACKEND_URL` if your backend runs elsewhere.

On Linux/macOS, you can also run both services from the repository root:

```bash
chmod +x ./run-hermes.sh
./run-hermes.sh
```

By default, project files are written under `./workspace/project_name` relative to the backend process. Set `Work Path` in the frontend to use a different local directory for the project workspace. The app rejects filesystem roots, existing file paths, invalid Windows path characters, reserved Windows device names, and control characters. `Work Path`, new project, generate, and load controls are locked while a project is running; pause or stop first.

Hermes profiles are loaded from `HERMES_PROFILE_ROOT` when set. Otherwise, Windows searches `%LOCALAPPDATA%\hermes\profiles`; Linux/macOS searches `$XDG_CONFIG_HOME/hermes/profiles`, `~/.config/hermes/profiles`, `~/.hermes/profiles`, and `~/.local/share/hermes/profiles`. If no profile directory or no `config.yaml` profiles are found, the frontend shows an error instead of using hardcoded fallback profiles.

Dashboard presets save the project goal, work path, and four profile slots including role, cautions, manager selection, and assigned profile. Presets are stored under `HERMES_DASHBOARD_ROOT` when set, otherwise under `./workspace/dashboard_presets`.

## Frontend Workflow

1. Enter the project goal.
2. Set `Work Path` if the project should write files outside the default workspace.
3. Optionally click `Save Dashboard` or `Load Dashboard` to persist or restore the current dashboard inputs.
4. Assign up to four Hermes profiles into the 2x2 agent slots grid using the `Select` buttons.
5. Mark exactly one assigned slot as `Manager`.
6. Fill in role and cautions for each participating profile.
7. Click `Generate To-do list` or `Load To-do list`.
8. Use the toolbar buttons: refresh for a new project, play to start/resume, pause to pause, and stop to terminate.

The UI prioritizes a state-of-the-art visual experience with explicit slot assignment and clearly readable project monitoring.
