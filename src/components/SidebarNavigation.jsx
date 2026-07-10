import { MessageSquare, Cpu, Brain, CheckSquare, Plug, Zap, Shield } from 'lucide-react';
import './SidebarNavigation.css';

const NAV_ITEMS = [
  { id: 'chat', label: 'Chat', Icon: MessageSquare },
  { id: 'ai', label: 'Local AI', Icon: Cpu },
  { id: 'memory', label: 'Memory', Icon: Brain },
  { id: 'tasks', label: 'Tasks', Icon: CheckSquare },
  { id: 'integrations', label: 'Integrations', Icon: Plug },
  { id: 'automations', label: 'Automations', Icon: Zap },
  { id: 'privacy', label: 'Privacy', Icon: Shield },
];

export default function SidebarNavigation({ activeScreen, onNavigate, userName, assistantName, onReset, backendStatus, modelAvailable }) {
  const getInitial = (name) => name ? name.charAt(0).toUpperCase() : 'U';

  return (
    <div className="sidebar-nav">
      <div className="sidebar-nav-brand">
        <div className="sidebar-nav-brand-icon">&#x2728;</div>
        <span className="sidebar-nav-brand-name">{assistantName}</span>
      </div>

      <nav className="sidebar-nav-items">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            className={`sidebar-nav-item ${activeScreen === item.id ? 'active' : ''}`}
            onClick={() => onNavigate(item.id)}
            title={item.label}
          >
            <span className="sidebar-nav-icon">
              <item.Icon size={16} strokeWidth={1.5} />
            </span>
            <span className="sidebar-nav-label">{item.label}</span>
            {item.id === 'chat' && backendStatus === 'ready' && !modelAvailable && (
              <span className="sidebar-nav-badge" title="No model loaded">!</span>
            )}
          </button>
        ))}
      </nav>

      <div className="sidebar-nav-footer">
        <div className="sidebar-nav-status">
          <span className={`sidebar-nav-status-dot ${backendStatus === 'ready' ? 'online' : backendStatus === 'connecting' ? 'loading' : 'offline'}`} />
          <span className="sidebar-nav-status-text">
            {backendStatus === 'ready' ? 'Connected' : backendStatus === 'connecting' ? 'Starting...' : 'Offline'}
          </span>
        </div>
        <div className="sidebar-nav-user" onClick={onReset} title="Reset & start over">
          <div className="sidebar-nav-avatar">{getInitial(userName)}</div>
          <span className="sidebar-nav-user-name">{userName}</span>
        </div>
      </div>
    </div>
  );
}
