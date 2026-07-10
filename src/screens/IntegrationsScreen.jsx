import { useState, useEffect } from 'react';
import { Folder, FileText, Globe, Plus } from 'lucide-react';
import './screens.css';

const BACKEND_URL = 'http://127.0.0.1:8765';

const MCP_SERVERS = [
  { id: 'filesystem', name: 'FileSystem', description: 'Read, write, and manage local files', icon: 'folder', status: 'available' },
  { id: 'notes', name: 'Notes', description: 'Create and organize personal notes', icon: 'note', status: 'available' },
  { id: 'browser', name: 'Browser', description: 'Web browsing and content extraction', icon: 'globe', status: 'available' },
];

const ICONS = {
  folder: <Folder size={16} strokeWidth={1.5} />,
  note: <FileText size={16} strokeWidth={1.5} />,
  globe: <Globe size={16} strokeWidth={1.5} />,
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
            <Plus size={24} strokeWidth={1.5} />
          </div>
          <h3 className="feature-card-title">Add Server</h3>
          <p className="feature-card-desc">Connect a custom MCP server</p>
        </div>
      </div>
    </div>
  );
}
