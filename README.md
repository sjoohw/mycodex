# Hermes Multi-Agent Project Management System

A lightweight prototype for a Hermes-based, maximum-four-agent project workspace.

## Features

- FastAPI backend with a single WebSocket event broker.
- SQLite plus JSON snapshot persistence under `/workspace/project_name/.hermes`.
- Tool endpoints for message passing, wiki reads/writes, bash execution, worker pings, human pause, and approval requests.
- React + React Flow frontend with agent nodes, animated message edges, filtered logs, and file tree monitoring.

## Run

```bash
pip install -e '.[dev]'
npm install
npm run backend
npm run frontend
```

Open the Vite URL and use the control panel to configure, assign roles, and start the project. Vite proxies `/api` and `/ws` to the FastAPI backend at `http://127.0.0.1:8000` by default; set `HERMES_BACKEND_URL` if your backend runs elsewhere.
