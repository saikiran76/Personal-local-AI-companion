import './SidebarNavigation.css';

const NAV_ITEMS = [
  { id: 'chat', label: 'Chat', icon: 'chat' },
  { id: 'ai', label: 'Local AI', icon: 'cpu' },
  { id: 'memory', label: 'Memory', icon: 'brain' },
  { id: 'tasks', label: 'Tasks', icon: 'check' },
  { id: 'integrations', label: 'Integrations', icon: 'plug' },
  { id: 'automations', label: 'Automations', icon: 'zap' },
  { id: 'privacy', label: 'Privacy', icon: 'shield' },
];

const ICONS = {
  chat: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
    </svg>
  ),
  cpu: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/>
      <line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/>
      <line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/>
      <line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/>
      <line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/>
    </svg>
  ),
  brain: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9.5 2A5.5 5.5 0 0 0 4 7.5c0 1.33.47 2.55 1.26 3.5H4a3 3 0 0 0 0 6h1.26c-.03.33-.06.66-.06 1a5.5 5.5 0 0 0 11 0c0-.34-.03-.67-.06-1H20a3 3 0 0 0 0-6h-1.26c.79-.95 1.26-2.17 1.26-3.5A5.5 5.5 0 0 0 14.5 2"/>
      <path d="M12 2v20"/>
    </svg>
  ),
  check: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
    </svg>
  ),
  plug: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/><path d="M18 8v5a6 6 0 0 1-6 6v0a6 6 0 0 1-6-6V8z"/>
    </svg>
  ),
  zap: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
    </svg>
  ),
  shield: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
    </svg>
  ),
};

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
            <span className="sidebar-nav-icon">{ICONS[item.id]}</span>
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
