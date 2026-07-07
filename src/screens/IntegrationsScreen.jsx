import { useState, useEffect } from 'react';
import './screens.css';

const BACKEND_URL = 'http://127.0.0.1:8765';

const MCP_SERVERS = [
  { id: 'filesystem', name: 'FileSystem', description: 'Read, write, and manage local files', icon: 'folder', status: 'available' },
  { id: 'notes', name: 'Notes', description: 'Create and organize personal notes', icon: 'note', status: 'available' },
  { id: 'browser', name: 'Browser', description: 'Web browsing and content extraction', icon: 'globe', status: 'available' },
];

const ICONS = {
  folder: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
    </svg>
  ),
  note: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
    </svg>
  ),
  globe: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
    </svg>
  ),
};

export default function IntegrationsScreen({ config }) {
  const [servers, setServers] = useState(MCP_SERVERS);

  return (
    <div className="screen-container">
      <div className="screen-header">
        <h1 className="screen-title">Integrations</h1>
        <p className="screen-subtitle">MCP server connections — extend AI capabilities with external tools</p>
      </div>

      <div className="screen-grid">
        {servers.map((server) => (
          <div key={server.id} className="feature-card">
            <div className="feature-card-header">
              <div className="feature-card-icon">{ICONS[server.icon]}</div>
              <span className={`badge badge-${server.status}`}>{server.status}</span>
            </div>
            <div className="feature-card-eyebrow">MCP SERVER</div>
            <h3 className="feature-card-title">{server.name}</h3>
            <p className="feature-card-desc">{server.description}</p>
            <div className="feature-card-footer">
              <button className="btn-ghost-sm">Configure</button>
              <button className="btn-primary-sm">{server.status === 'active' ? 'Disconnect' : 'Connect'}</button>
            </div>
          </div>
        ))}

        {/* Add Custom Server */}
        <div className="feature-card feature-card-add">
          <div className="feature-card-add-icon">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
            </svg>
          </div>
          <h3 className="feature-card-title">Add Server</h3>
          <p className="feature-card-desc">Connect a custom MCP server</p>
        </div>
      </div>
    </div>
  );
}
