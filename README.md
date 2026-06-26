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

## Run

```bash
pip install -e '.[dev]'
npm install
npm run backend
npm run frontend
```

Open the Vite URL and use the control panel to create a project, generate or load a to-do list, and run/pause/stop execution. Vite proxies `/api` and `/ws` to the FastAPI backend at `http://127.0.0.1:8000` by default; set `HERMES_BACKEND_URL` if your backend runs elsewhere.

By default, project files are written under `./workspace/project_name` relative to the backend process. Set `Work Path` in the frontend to use a different local directory for the project workspace. The app rejects filesystem roots, existing file paths, invalid Windows path characters, reserved Windows device names, and control characters. `Work Path`, new project, generate, and load controls are locked while a project is running; pause or stop first.

## Frontend Workflow

1. Enter the project goal.
2. Set `Work Path` if the project should write files outside the default workspace.
3. Assign up to four Hermes profiles into the 2x2 agent slots grid using the `Select` buttons.
4. Mark exactly one assigned slot as `Manager`.
5. Fill in role and cautions for each participating profile.
6. Click `Generate To-do list` or `Load To-do list`.
7. Use the toolbar buttons: refresh for a new project, play to start/resume, pause to pause, and stop to terminate.

The UI prioritizes a state-of-the-art visual experience with explicit slot assignment and clearly readable project monitoring.
