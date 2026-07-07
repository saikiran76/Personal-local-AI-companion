import { useState, useEffect } from 'react';
import './screens.css';

export default function MemoryScreen({ config }) {
  const [memories, setMemories] = useState([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeCategory, setActiveCategory] = useState('all');

  const categories = [
    { id: 'all', label: 'All' },
    { id: 'documents', label: 'Documents' },
    { id: 'notes', label: 'Notes' },
    { id: 'contacts', label: 'Contacts' },
    { id: 'preferences', label: 'Preferences' },
  ];

  return (
    <div className="screen-container">
      <div className="screen-header">
        <h1 className="screen-title">Memory</h1>
        <p className="screen-subtitle">Your personal knowledge base — everything the AI remembers about you</p>
      </div>

      <div className="screen-toolbar">
        <div className="search-input-wrapper">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input
            type="text"
            className="search-input"
            placeholder="Search memories..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
        <div className="category-pills">
          {categories.map((cat) => (
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

      <div className="screen-grid">
        <div className="feature-card feature-card-wide">
          <div className="feature-card-eyebrow">KNOWLEDGE BASE</div>
          <h3 className="feature-card-title">Stored Memories</h3>
          <div className="feature-card-body">
            {memories.length === 0 ? (
              <div className="feature-card-empty">
                <div className="empty-icon">
                  <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2z"/>
                    <path d="M12 6v6l4 2"/>
                  </svg>
                </div>
                <p>No memories stored yet</p>
                <p className="empty-hint">Conversations and documents will be indexed here as you use the app</p>
              </div>
            ) : (
              <div className="memory-list">
                {memories.map((m, i) => (
                  <div key={i} className="memory-item">
                    <div className="memory-item-category">{m.category}</div>
                    <div className="memory-item-content">{m.content}</div>
                    <div className="memory-item-meta">{m.timestamp}</div>
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
              <span className="info-label">Categories</span>
              <span className="info-value">{categories.length - 1}</span>
            </div>
            <div className="info-row">
              <span className="info-label">Storage</span>
              <span className="info-value">Local JSON</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
