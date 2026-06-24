import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import ReactFlow, { Background, Controls, Handle, Position } from 'reactflow';
import 'reactflow/dist/style.css';
import './style.css';

const initialProfiles = [
  { id: 'manager', name: 'Manager', role: 'Plans, delegates, and asks humans', is_manager: true, status: 'idle' },
  { id: 'worker-1', name: 'Builder', role: 'Implements files and tests', is_manager: false, status: 'idle' },
  { id: 'worker-2', name: 'Reviewer', role: 'Reviews plans and results', is_manager: false, status: 'idle' },
  { id: 'worker-3', name: 'Runner', role: 'Executes bash checks', is_manager: false, status: 'idle' },
];

function AgentNode({ data }) {
  return <div className={`agent-node ${data.status === 'working' ? 'working' : ''}`}>
    <Handle type="target" position={Position.Left} />
    <strong>{data.name}</strong><span>{data.is_manager ? 'Manager' : data.role}</span><small>{data.status}</small>
    <Handle type="source" position={Position.Right} />
  </div>;
}
const nodeTypes = { agent: AgentNode };

function App() {
  const [goal, setGoal] = useState('Build a resilient web application with clear tests.');
  const [profiles, setProfiles] = useState(initialProfiles);
  const [state, setState] = useState(null);
  const [selected, setSelected] = useState('manager');

  useEffect(() => {
    const ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onmessage = (message) => {
      const payload = JSON.parse(message.data);
      if (payload.state) setState(payload.state);
    };
    return () => ws.close();
  }, []);

  const activeProfiles = state?.config?.profiles || profiles;
  const events = state?.events || [];
  const nodes = useMemo(() => activeProfiles.map((profile, index) => ({
    id: profile.id,
    type: 'agent',
    position: { x: index % 2 ? 520 : 120, y: index < 2 ? 80 : 320 },
    data: profile,
  })), [activeProfiles]);
  const edges = events.filter((event) => event.type === 'message' && event.target).slice(-8).map((event) => ({
    id: event.id,
    source: event.source,
    target: event.target,
    animated: true,
    label: event.metadata?.message_type || 'message',
  }));
  const visibleEvents = events.filter((event) => !selected || event.source === selected || event.target === selected);

  async function post(path, body) {
    const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    setState(await response.json());
  }

  return <main className="shell">
    <aside className="panel control">
      <h1>Hermes Workspace</h1>
      <label>Project Goal<textarea value={goal} onChange={(event) => setGoal(event.target.value)} /></label>
      <button onClick={() => post('/api/configure', { name: 'hermes-project', goal, profiles })}>Configure</button>
      <button onClick={() => post('/api/assign-roles')}>역할 분배</button>
      <button onClick={() => post('/api/start')}>Project Start</button>
      <button onClick={() => post('/api/stop')}>Stop / Pause</button>
      <p>Status: <b>{state?.status || 'not configured'}</b></p>
    </aside>
    <section className="canvas">
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodeClick={(_, node) => setSelected(node.id)} fitView>
        <Background /><Controls />
      </ReactFlow>
    </section>
    <aside className="panel monitor">
      <h2>Monitoring</h2>
      <p>Selected: {selected}</p>
      <h3>Logs</h3>
      <div className="logs">{visibleEvents.map((event) => <article key={event.id}><b>{event.type}</b><span>{event.source}{event.target ? ` → ${event.target}` : ''}</span><p>{event.content}</p></article>)}</div>
      <h3>Files</h3>
      <ul>{(state?.files || []).map((file) => <li key={file}>{file}</li>)}</ul>
    </aside>
  </main>;
}

createRoot(document.getElementById('root')).render(<App />);
