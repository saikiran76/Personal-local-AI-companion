import { useState } from 'react';
import './screens.css';

export default function TasksScreen({ config }) {
  const [tasks, setTasks] = useState([]);
  const [newTask, setNewTask] = useState('');
  const [activeFilter, setActiveFilter] = useState('all');

  const filters = [
    { id: 'all', label: 'All' },
    { id: 'pending', label: 'Pending' },
    { id: 'running', label: 'Running' },
    { id: 'done', label: 'Done' },
  ];

  const addTask = () => {
    if (!newTask.trim()) return;
    setTasks([...tasks, {
      id: Date.now(),
      title: newTask.trim(),
      status: 'pending',
      created: new Date(),
    }]);
    setNewTask('');
  };

  const toggleTask = (id) => {
    setTasks(tasks.map(t =>
      t.id === id ? { ...t, status: t.status === 'done' ? 'pending' : 'done' } : t
    ));
  };

  const filtered = activeFilter === 'all' ? tasks : tasks.filter(t => t.status === activeFilter);

  return (
    <div className="screen-container">
      <div className="screen-header">
        <h1 className="screen-title">Tasks</h1>
        <p className="screen-subtitle">Desktop task assistant — automate file operations and workflows</p>
      </div>

      <div className="screen-toolbar">
        <div className="search-input-wrapper">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
          </svg>
          <input
            type="text"
            className="search-input"
            placeholder="Add a new task..."
            value={newTask}
            onChange={(e) => setNewTask(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && addTask()}
          />
        </div>
        <div className="category-pills">
          {filters.map((f) => (
            <button
              key={f.id}
              className={`category-pill ${activeFilter === f.id ? 'active' : ''}`}
              onClick={() => setActiveFilter(f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      <div className="screen-grid">
        <div className="feature-card feature-card-wide">
          <div className="feature-card-eyebrow">TASK QUEUE</div>
          <h3 className="feature-card-title">Your Tasks</h3>
          <div className="feature-card-body">
            {filtered.length === 0 ? (
              <div className="feature-card-empty">
                <div className="empty-icon">
                  <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
                  </svg>
                </div>
                <p>No tasks yet</p>
                <p className="empty-hint">Add a task above or ask the AI to help organize your work</p>
              </div>
            ) : (
              <div className="task-list">
                {filtered.map((task) => (
                  <div key={task.id} className={`task-item ${task.status}`}>
                    <button
                      className={`task-checkbox ${task.status === 'done' ? 'checked' : ''}`}
                      onClick={() => toggleTask(task.id)}
                    >
                      {task.status === 'done' && (
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="20 6 9 17 4 12"/>
                        </svg>
                      )}
                    </button>
                    <span className="task-title">{task.title}</span>
                    <span className={`task-status-badge ${task.status}`}>{task.status}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="feature-card">
          <div className="feature-card-eyebrow">QUICK ACTIONS</div>
          <h3 className="feature-card-title">Automations</h3>
          <div className="feature-card-body">
            <div className="action-list">
              <button className="action-item">
                <span className="action-icon">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>
                  </svg>
                </span>
                Organize downloads
              </button>
              <button className="action-item">
                <span className="action-icon">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/>
                  </svg>
                </span>
                Clean desktop
              </button>
              <button className="action-item">
                <span className="action-icon">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
                  </svg>
                </span>
                Schedule backup
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
