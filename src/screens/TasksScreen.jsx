import { useState } from 'react';
import { Plus, CheckSquare, Check, FileText, Layout, Clock } from 'lucide-react';
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
          <Plus size={14} strokeWidth={2} />
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
                  <CheckSquare size={32} strokeWidth={1} />
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
                        <Check size={10} strokeWidth={3} />
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
                  <FileText size={14} strokeWidth={1.5} />
                </span>
                Organize downloads
              </button>
              <button className="action-item">
                <span className="action-icon">
                  <Layout size={14} strokeWidth={1.5} />
                </span>
                Clean desktop
              </button>
              <button className="action-item">
                <span className="action-icon">
                  <Clock size={14} strokeWidth={1.5} />
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
