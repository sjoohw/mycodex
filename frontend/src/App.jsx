import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

const defaultCatalog = [
  { id: 'layout_architect', name: 'Layout Architect', hermes_profile: 'layout_architect' },
  { id: 'layout_designer', name: 'Layout Designer', hermes_profile: 'layout_designer' },
  { id: 'layout_qa', name: 'Layout QA', hermes_profile: 'layout_qa' },
];

const defaultSlots = [
  {
    id: 'slot_1',
    profile: defaultCatalog[0],
    role: 'Plans structure, routes work, and synthesizes final review',
    cautions: 'Ask for human input only on real blockers.',
    is_manager: true,
  },
  {
    id: 'slot_2',
    profile: defaultCatalog[1],
    role: 'Implements the requested deliverables',
    cautions: 'Do not block just because QA is pending.',
    is_manager: false,
  },
  {
    id: 'slot_3',
    profile: defaultCatalog[2],
    role: 'Verifies outputs, regressions, and final readiness',
    cautions: 'Use concrete file and command evidence.',
    is_manager: false,
  },
  { id: 'slot_4', profile: null, role: '', cautions: '', is_manager: false },
];

function emptySlot(index) {
  return { id: `slot_${index + 1}`, profile: null, role: '', cautions: '', is_manager: false };
}

function App() {
  const [goal, setGoal] = useState('Build a resilient web application with clear tests.');
  const [workPath, setWorkPath] = useState('');
  const [profileCatalog, setProfileCatalog] = useState(defaultCatalog);
  const [slots, setSlots] = useState(defaultSlots);
  const [state, setState] = useState(null);
  const [apiStatus, setApiStatus] = useState('Ready');
  const [isPosting, setIsPosting] = useState(false);
  const [pickerSlot, setPickerSlot] = useState(null);
  const [confirmTodo, setConfirmTodo] = useState(false);
  const [todoEditorOpen, setTodoEditorOpen] = useState(false);
  const [todoContent, setTodoContent] = useState('');
  const [todoDraft, setTodoDraft] = useState('');
  const [todoPath, setTodoPath] = useState('');
  const [loadOpen, setLoadOpen] = useState(false);
  const [mdFiles, setMdFiles] = useState([]);
  const [logAgent, setLogAgent] = useState(null);

  useEffect(() => {
    fetch('/api/profiles')
      .then((response) => response.ok ? response.json() : [])
      .then((profiles) => {
        if (profiles.length) setProfileCatalog(profiles);
      })
      .catch(() => {});

    fetch('/api/state')
      .then((response) => response.ok ? response.json() : null)
      .then((snapshot) => {
        if (snapshot) {
          setState(snapshot);
          if (snapshot.config?.workspace_root) setWorkPath(snapshot.config.workspace_root);
        }
      })
      .catch(() => setApiStatus('Backend is not reachable.'));

    const wsProtocol = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${wsProtocol}://${location.host}/ws`);
    ws.onopen = () => setApiStatus('Realtime connected');
    ws.onmessage = (message) => {
      const payload = JSON.parse(message.data);
      if (payload.state) setState(payload.state);
    };
    ws.onerror = () => setApiStatus('Realtime connection failed. API buttons may still work.');
    ws.onclose = () => setApiStatus('Realtime disconnected');
    return () => ws.close();
  }, []);

  const configuredProfiles = useMemo(() => {
    const assigned = slots.filter((slot) => slot.profile);
    const managerId = assigned.find((slot) => slot.is_manager)?.id || assigned[0]?.id;
    return assigned.map((slot) => ({
      id: slot.profile.id,
      name: slot.profile.name,
      role: slot.role || 'Worker',
      cautions: slot.cautions || '',
      hermes_profile: slot.profile.hermes_profile,
      is_manager: slot.id === managerId,
      status: 'idle',
    }));
  }, [slots]);

  const projectConfig = useMemo(() => ({
    name: 'hermes-project',
    goal,
    workspace_root: workPath.trim() || null,
    profiles: configuredProfiles,
  }), [goal, workPath, configuredProfiles]);

  const activeProfiles = state?.config?.profiles || configuredProfiles;
  const events = state?.events || [];
  const tasks = state?.kanban?.tasks || [];
  const projectRunning = state?.status === 'running';
  const todoSteps = useMemo(() => parseTodoSteps(todoContent), [todoContent]);
  const currentTask = tasks.find((task) => task.status === 'running') || tasks.find((task) => ['ready', 'todo', 'review'].includes(task.status));

  useEffect(() => {
    const path = state?.todo_path;
    if (!path) return;
    fetch(`/api/todo?path=${encodeURIComponent(path)}`)
      .then((response) => response.ok ? response.json() : null)
      .then((todo) => {
        if (!todo) return;
        setTodoPath(todo.path || '');
        setTodoContent(todo.content || '');
        setTodoDraft(todo.content || '');
      })
      .catch(() => {});
  }, [state?.todo_path, state?.events?.length]);

  function parseTodoSteps(content) {
    return content.split('\n')
      .filter((line) => /^-\s+\[[ xX]\]/.test(line.trim()))
      .map((line) => ({
        text: line.replace(/^-\s+\[[ xX]\]\s*/, '').trim(),
        done: /^-\s+\[[xX]\]/.test(line.trim()),
      }));
  }

  function assignProfile(slotId, profile) {
    setSlots((current) => {
      const duplicateSlot = current.find((slot) => slot.profile?.id === profile.id);
      const targetSlot = current.find((slot) => slot.id === slotId);
      const otherManagerExists = current.some((slot) => slot.id !== slotId && slot.profile?.id !== profile.id && slot.is_manager);
      const shouldBeManager = Boolean(targetSlot?.is_manager || duplicateSlot?.is_manager || !otherManagerExists);
      return current.map((slot) => {
        if (slot.id !== slotId && slot.profile?.id === profile.id) return emptySlot(Number(slot.id.replace('slot_', '')) - 1);
        if (slot.id !== slotId) return slot;
        return {
          ...slot,
          profile,
          is_manager: shouldBeManager,
          role: slot.role || (slot.is_manager ? 'Plans and manages work' : 'Executes assigned work'),
        };
      });
    });
    setPickerSlot(null);
  }

  function clearSlot(slotId) {
    setSlots((current) => {
      const cleared = current.map((slot, index) => slot.id === slotId ? emptySlot(index) : slot);
      if (cleared.some((slot) => slot.profile && slot.is_manager)) return cleared;
      const firstAssigned = cleared.find((slot) => slot.profile);
      return cleared.map((slot) => slot.id === firstAssigned?.id ? { ...slot, is_manager: true } : slot);
    });
  }

  function updateSlot(slotId, patch) {
    setSlots((current) => current.map((slot) => slot.id === slotId ? { ...slot, ...patch } : slot));
  }

  function setManager(slotId) {
    setSlots((current) => current.map((slot) => ({ ...slot, is_manager: slot.id === slotId })));
  }

  async function post(path, body) {
    setIsPosting(true);
    setApiStatus(`Sending ${path}`);
    try {
      const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
      const text = await response.text();
      if (!response.ok) throw new Error(errorMessage(text, `${response.status} ${response.statusText}`));
      const nextState = text ? JSON.parse(text) : null;
      if (nextState) setState(nextState);
      setApiStatus(`${path} completed`);
      return nextState;
    } catch (error) {
      setApiStatus(error instanceof Error ? error.message : 'Request failed');
      return null;
    } finally {
      setIsPosting(false);
    }
  }

  function requireProfiles() {
    if (configuredProfiles.length > 0) return true;
    setApiStatus('Error: assign at least one profile first.');
    return false;
  }

  function requestGenerateTodo() {
    if (!requireProfiles()) return;
    if (projectRunning) {
      setApiStatus('Pause or stop before changing project settings.');
      return;
    }
    setConfirmTodo(true);
  }

  async function generateTodo() {
    const nextState = await post('/api/generate-todo', projectConfig);
    setConfirmTodo(false);
    if (nextState?.todo_path) setTodoEditorOpen(true);
  }

  async function newProject() {
    if (!requireProfiles()) return;
    if (projectRunning) {
      setApiStatus('Pause or stop before changing project settings.');
      return;
    }
    setTodoContent('');
    setTodoDraft('');
    setTodoPath('');
    await post('/api/new-project', projectConfig);
  }

  async function openLoadTodo() {
    if (!requireProfiles()) return;
    if (projectRunning) {
      setApiStatus('Pause or stop before changing project settings.');
      return;
    }
    const query = workPath.trim() ? `?workspace_root=${encodeURIComponent(workPath.trim())}` : '';
    const response = await fetch(`/api/todo-files${query}`);
    const text = await response.text();
    if (!response.ok) {
      setApiStatus(errorMessage(text, 'Could not load markdown files.'));
      setMdFiles([]);
      return;
    }
    setMdFiles(text ? JSON.parse(text) : []);
    setLoadOpen(true);
  }

  async function loadTodo(path) {
    await post('/api/load-todo', { config: projectConfig, path });
    setLoadOpen(false);
  }

  async function saveTodo() {
    const nextState = await post('/api/save-todo', { path: todoPath || 'shared/todo.md', content: todoDraft });
    if (nextState) {
      setTodoContent(todoDraft);
      setTodoPath(nextState.todo_path || todoPath || 'shared/todo.md');
    }
  }

  function agentEvents(agentId) {
    return events.filter((event) => event.source === agentId || event.target === agentId);
  }

  return <main className="shell">
    <aside className="panel control">
      <h1>Hermes Workspace</h1>
      <label>Project Goal<textarea value={goal} onChange={(event) => setGoal(event.target.value)} /></label>
      <label>Work Path<input type="text" value={workPath} disabled={projectRunning} placeholder="Default: ./workspace/project_name" onChange={(event) => setWorkPath(event.target.value)} /></label>
      <div className="toolbar">
        <button title="New project" disabled={isPosting || projectRunning} onClick={newProject}>↻</button>
        <button title="Play" disabled={isPosting} onClick={() => post('/api/start')}>▶</button>
        <button title="Pause" disabled={isPosting} onClick={() => post('/api/pause')}>⏸</button>
        <button title="Stop" disabled={isPosting} onClick={() => post('/api/terminate')}>■</button>
      </div>
      <button disabled={isPosting || projectRunning} onClick={requestGenerateTodo}>Generate To-do list</button>
      <button disabled={isPosting || projectRunning} onClick={openLoadTodo}>Load To-do list</button>
      {todoPath && <button className="doc-button" onClick={() => { setTodoDraft(todoContent); setTodoEditorOpen(true); }}>📄 {todoPath}</button>}
      <p className={`api-status ${apiStatus.toLowerCase().includes('failed') || apiStatus.toLowerCase().includes('error') ? 'error' : ''}`}>{apiStatus}</p>
      <p>Status: <b>{state?.status || 'not configured'}</b></p>
      <p>Path: <b>{state?.config?.workspace_root || workPath || 'default'}</b></p>
      <p>Board: <b>{state?.board_slug || '-'}</b></p>
    </aside>

    <section className="workspace">
      <section className="slots">
        {slots.map((slot, index) => {
          const runtime = activeProfiles.find((profile) => profile.id === slot.profile?.id || profile.hermes_profile === slot.profile?.hermes_profile);
          return <article key={slot.id} className={`slot ${slot.profile ? 'assigned' : ''}`}>
            <header>
              <div>
                <b>Slot {index + 1}</b>
                <span>{slot.profile ? `${slot.profile.name} · ${runtime?.status || 'idle'}` : 'Select a profile'}</span>
              </div>
              <div className="slot-actions">
                <button type="button" onClick={() => setPickerSlot(pickerSlot === slot.id ? null : slot.id)}>Select</button>
                {slot.profile && <button type="button" onClick={() => clearSlot(slot.id)}>Clear</button>}
              </div>
            </header>

            {pickerSlot === slot.id && <div className="picker">
              {profileCatalog.map((profile) => <button key={profile.id} type="button" onClick={() => assignProfile(slot.id, profile)}>{profile.name}</button>)}
            </div>}

            {slot.profile && <>
              <label className="manager-toggle">
                <input type="radio" checked={slot.is_manager} onChange={() => setManager(slot.id)} />
                Manager
              </label>
              <label>Role<textarea value={slot.role} onChange={(event) => updateSlot(slot.id, { role: event.target.value })} /></label>
              <label>Cautions<textarea value={slot.cautions} onChange={(event) => updateSlot(slot.id, { cautions: event.target.value })} /></label>
            </>}
          </article>;
        })}
      </section>
    </section>

    <aside className="panel monitor">
      <h2>Monitoring</h2>
      <h3>Agents</h3>
      <div className="tasks">{activeProfiles.map((profile) => <button className="agent-row" key={profile.id} onClick={() => setLogAgent(profile)}>
        <b>{profile.status}</b><span>{profile.hermes_profile}</span><p>{profile.name}</p>
      </button>)}</div>
      <h3>To-do List</h3>
      <div className="todo-panel">
        {currentTask && <p className="current-step"><b>Current</b>{currentTask.title}</p>}
        {todoSteps.length ? todoSteps.map((step, index) => <article key={`${step.text}-${index}`} className={`${step.done ? 'done' : ''} ${!step.done && index === todoSteps.findIndex((item) => !item.done) ? 'current' : ''}`}>
          <span>{index + 1}</span>
          <p>{step.text}</p>
        </article>) : <p>No to-do list loaded.</p>}
      </div>
      <h3>Files</h3>
      <ul>{(state?.files || []).map((file) => <li key={file}>{file}</li>)}</ul>
      <h3>Kanban Tasks</h3>
      <div className="tasks">{tasks.map((task) => <article key={task.id}><b>{task.status}</b><span>{task.assignee || 'unassigned'}</span><p>{task.title}</p></article>)}</div>
    </aside>

    {confirmTodo && <Modal title="Generate To-do list" onClose={() => setConfirmTodo(false)}>
      <p>Proceed with these profiles?</p>
      <div className="modal-list">{configuredProfiles.map((profile) => <article key={profile.id}>
        <b>{profile.name}{profile.is_manager ? ' · Manager' : ''}</b>
        <span>{profile.hermes_profile}</span>
        <p>{profile.role}</p>
        {profile.cautions && <p>Cautions: {profile.cautions}</p>}
      </article>)}</div>
      <div className="modal-actions">
        <button onClick={generateTodo}>Generate</button>
        <button onClick={() => setConfirmTodo(false)}>Cancel</button>
      </div>
    </Modal>}

    {todoEditorOpen && <Modal title={todoPath || 'To-do list'} onClose={() => setTodoEditorOpen(false)}>
      <textarea className="todo-editor" value={todoDraft} onChange={(event) => setTodoDraft(event.target.value)} />
      <div className="modal-actions">
        <button onClick={saveTodo}>Save</button>
        <button onClick={() => setTodoEditorOpen(false)}>Close</button>
      </div>
    </Modal>}

    {loadOpen && <Modal title="Load To-do list" onClose={() => setLoadOpen(false)}>
      <div className="modal-list">{mdFiles.length ? mdFiles.map((file) => <button key={file} onClick={() => loadTodo(file)}>{file}</button>) : <p>No markdown files found.</p>}</div>
    </Modal>}

    {logAgent && <Modal title={`${logAgent.name} Log`} onClose={() => setLogAgent(null)}>
      <div className="logs">{agentEvents(logAgent.id).length ? agentEvents(logAgent.id).map((event) => <article key={event.id}>
        <b>{event.type}</b>
        <span>{event.source}{event.target ? ` -> ${event.target}` : ''}</span>
        <p>{event.content}</p>
      </article>) : <p>No log for this agent.</p>}</div>
    </Modal>}
  </main>;
}

function errorMessage(text, fallback) {
  if (!text) return fallback;
  try {
    const payload = JSON.parse(text);
    return payload.detail || fallback;
  } catch {
    return text;
  }
}

function Modal({ title, children, onClose }) {
  return <div className="modal-backdrop" onMouseDown={onClose}>
    <section className="modal" onMouseDown={(event) => event.stopPropagation()}>
      <header><h2>{title}</h2><button onClick={onClose}>×</button></header>
      {children}
    </section>
  </div>;
}

createRoot(document.getElementById('root')).render(<App />);
