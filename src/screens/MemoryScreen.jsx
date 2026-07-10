import { useState, useEffect, useCallback } from 'react';
import { Search } from 'lucide-react';
import './screens.css';

const BACKEND_URL = 'http://127.0.0.1:8765';

const CATEGORIES = [
  { id: 'all', label: 'All' },
  { id: 'fact', label: 'Facts' },
  { id: 'preference', label: 'Preferences' },
  { id: 'note', label: 'Notes' },
  { id: 'other', label: 'Other' },
];

export default function MemoryScreen({ config }) {
  const [memories, setMemories] = useState([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeCategory, setActiveCategory] = useState('all');
  const [newFact, setNewFact] = useState('');
  const [newCategory, setNewCategory] = useState('fact');
  const [adding, setAdding] = useState(false);

  const loadMemories = useCallback(async () => {
    try {
      const url = searchQuery
        ? `${BACKEND_URL}/memories/search?q=${encodeURIComponent(searchQuery)}`
        : `${BACKEND_URL}/memories`;
      const res = await fetch(url);
      const data = await res.json();
      setMemories(data.memories || []);
    } catch {
      setMemories([]);
    }
  }, [searchQuery]);

  useEffect(() => {
    loadMemories();
  }, [loadMemories]);

  const addMemory = async () => {
    if (!newFact.trim()) return;
    setAdding(true);
    try {
      await fetch(`${BACKEND_URL}/memories`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category: newCategory, content: newFact.trim() }),
      });
      setNewFact('');
      loadMemories();
    } catch {
      // silent
    }
    setAdding(false);
  };

  const deleteMemory = async (id) => {
    try {
      await fetch(`${BACKEND_URL}/memories/${id}`, { method: 'DELETE' });
      setMemories((prev) => prev.filter((m) => m.id !== id));
    } catch {
      // silent
    }
  };

  const filtered = activeCategory === 'all'
    ? memories
    : memories.filter((m) => m.category === activeCategory);

  return (
    <div className="screen-container">
      <div className="screen-header">
        <h1 className="screen-title">Memory</h1>
        <p className="screen-subtitle">Your personal knowledge base — what the assistant knows about you</p>
      </div>

      <div className="screen-grid">
        {/* Add Personal Fact */}
        <div className="feature-card feature-card-wide">
          <div className="feature-card-eyebrow">ADD FACT</div>
          <h3 className="feature-card-title">Tell me about yourself</h3>
          <div className="feature-card-body">
            <p className="feature-card-desc" style={{ marginBottom: 12 }}>
              Add personal facts the assistant should always know — your name, preferences, work, etc.
            </p>
            <div className="memory-add-row">
              <select
                className="text-input memory-category-select"
                value={newCategory}
                onChange={(e) => setNewCategory(e.target.value)}
              >
                <option value="fact">Fact</option>
                <option value="preference">Preference</option>
                <option value="note">Note</option>
                <option value="other">Other</option>
              </select>
              <input
                type="text"
                className="text-input memory-input"
                placeholder='e.g. "My name is Alex", "I prefer dark mode", "I work at Acme Corp"'
                value={newFact}
                onChange={(e) => setNewFact(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && addMemory()}
                disabled={adding}
              />
              <button
                className="btn-primary-sm"
                onClick={addMemory}
                disabled={adding || !newFact.trim()}
              >
                {adding ? '...' : 'Add'}
              </button>
            </div>
          </div>
        </div>

        {/* Search + Filter */}
        <div className="feature-card feature-card-wide">
          <div className="feature-card-eyebrow">MEMORIES</div>
          <h3 className="feature-card-title">Stored Memories</h3>
          <div className="feature-card-body">
            <div className="screen-toolbar" style={{ marginBottom: 12 }}>
              <div className="search-input-wrapper">
                <Search size={14} strokeWidth={2} />
                <input
                  type="text"
                  className="search-input"
                  placeholder="Search memories..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                />
              </div>
              <div className="category-pills">
                {CATEGORIES.map((cat) => (
                  <button
                    key={cat.id}
                    className={`category-pill ${activeCategory === cat.id ? 'active' : ''}`}
                    onClick={() => setActiveCategory(cat.id)}
                  >
                    {cat.label}
                  </button>
                ))}
              </div>
            </div>

            {filtered.length === 0 ? (
              <div className="feature-card-empty">
                <p>No memories yet</p>
                <p className="empty-hint">Add a fact above or say "remember that..." in chat</p>
              </div>
            ) : (
              <div className="memory-list">
                {filtered.map((m) => (
                  <div key={m.id} className="memory-item">
                    <div className="memory-item-header">
                      <span className="memory-item-category">{m.category}</span>
                      <button
                        className="memory-delete-btn"
                        onClick={() => deleteMemory(m.id)}
                        title="Delete"
                      >
                        ×
                      </button>
                    </div>
                    <div className="memory-item-content">{m.content}</div>
                    <div className="memory-item-meta">
                      {m.created_at ? new Date(m.created_at).toLocaleDateString() : ''}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Stats */}
        <div className="feature-card">
          <div className="feature-card-eyebrow">STATS</div>
          <h3 className="feature-card-title">Knowledge Stats</h3>
          <div className="feature-card-body">
            <div className="info-row">
              <span className="info-label">Total Memories</span>
              <span className="info-value">{memories.length}</span>
            </div>
            <div className="info-row">
              <span className="info-label">Storage</span>
              <span className="info-value">SQLite (local)</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
