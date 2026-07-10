import { useState } from 'react';
import { Plus, Zap } from 'lucide-react';
import './screens.css';

const AUTOMATIONS = [
  { id: 'organize-downloads', name: 'Organize Downloads', description: 'Sort files in Downloads by type and date', trigger: 'Manual', status: 'ready' },
  { id: 'daily-summary', name: 'Daily Summary', description: 'Generate a summary of today\'s activity', trigger: 'Scheduled', status: 'ready' },
  { id: 'backup-notes', name: 'Backup Notes', description: 'Export notes to a backup location', trigger: 'Manual', status: 'ready' },
  { id: 'clean-desktop', name: 'Clean Desktop', description: 'Move desktop files to organized folders', trigger: 'Manual', status: 'ready' },
];

export default function AutomationsScreen({ config }) {
  const [automations, setAutomations] = useState(AUTOMATIONS);

  return (
    <div className="screen-container">
      <div className="screen-header">
        <h1 className="screen-title">Automations</h1>
        <p className="screen-subtitle">Intelligent workflows — let AI handle repetitive tasks</p>
      </div>

      <div className="screen-toolbar">
        <button className="btn-primary">
          <Plus size={14} strokeWidth={2} />
          New Automation
        </button>
      </div>

      <div className="screen-grid">
        {automations.map((auto) => (
          <div key={auto.id} className="feature-card">
            <div className="feature-card-header">
              <div className="feature-card-icon">
                <Zap size={16} strokeWidth={1.5} />
              </div>
              <span className={`badge badge-${auto.status}`}>{auto.status}</span>
            </div>
            <div className="feature-card-eyebrow">{auto.trigger.toUpperCase()}</div>
            <h3 className="feature-card-title">{auto.name}</h3>
            <p className="feature-card-desc">{auto.description}</p>
            <div className="feature-card-footer">
              <button className="btn-ghost-sm">Edit</button>
              <button className="btn-primary-sm">Run</button>
            </div>
          </div>
        ))}

        {/* Template cards */}
        <div className="feature-card feature-card-add">
          <div className="feature-card-add-icon">
            <Plus size={24} strokeWidth={1.5} />
          </div>
          <h3 className="feature-card-title">Create Custom</h3>
          <p className="feature-card-desc">Build your own automation workflow</p>
        </div>
      </div>
    </div>
  );
}
