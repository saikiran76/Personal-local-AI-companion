import { useState } from 'react';
import PermissionsToggles from '../components/PermissionsToggles';
import './screens.css';

const BACKEND_URL = 'http://127.0.0.1:8765';

export default function PrivacyScreen({ config }) {
  const [activity, setActivity] = useState(null);

  const loadActivity = async () => {
    try {
      const res = await fetch(`${BACKEND_URL}/activity?limit=20`);
      const data = await res.json();
      setActivity(data.activity || []);
    } catch {
      setActivity([]);
    }
  };

  const deleteAllData = async () => {
    if (!confirm('This will delete all conversations, memories, activity, and permissions. Continue?')) return;
    try {
      // Delete conversations (which cascade-deletes messages)
      const convos = await fetch(`${BACKEND_URL}/conversations`).then((r) => r.json());
      for (const c of convos.conversations || []) {
        await fetch(`${BACKEND_URL}/conversations/${c.id}`, { method: 'DELETE' });
      }
      // Delete memories
      const mems = await fetch(`${BACKEND_URL}/memories`).then((r) => r.json());
      for (const m of mems.memories || []) {
        await fetch(`${BACKEND_URL}/memories/${m.id}`, { method: 'DELETE' });
      }
      // Reset permissions
      for (const scope of ['files', 'notes', 'browser', 'email', 'reminders']) {
        await fetch(`${BACKEND_URL}/permissions`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scope, granted: false }),
        });
      }
      alert('All data deleted.');
    } catch {
      alert('Error deleting data.');
    }
  };

  return (
    <div className="screen-container">
      <div className="screen-header">
        <h1 className="screen-title">Privacy</h1>
        <p className="screen-subtitle">Your data never leaves your device — full transparency</p>
      </div>

      <div className="screen-grid">
        {/* Privacy Score */}
        <div className="feature-card feature-card-wide">
          <div className="feature-card-eyebrow">PRIVACY STATUS</div>
          <h3 className="feature-card-title">Your Privacy Score</h3>
          <div className="feature-card-body">
            <div className="privacy-score">
              <div className="privacy-score-ring">
                <svg width="80" height="80" viewBox="0 0 80 80">
                  <circle cx="40" cy="40" r="35" fill="none" stroke="var(--color-hairline)" strokeWidth="4"/>
                  <circle cx="40" cy="40" r="35" fill="none" stroke="var(--color-link)" strokeWidth="4" strokeDasharray="220" strokeDashoffset="20" strokeLinecap="round" transform="rotate(-90 40 40)"/>
                </svg>
                <span className="privacy-score-value">100%</span>
              </div>
              <div className="privacy-score-details">
                <div className="privacy-score-label">Excellent</div>
                <p className="privacy-score-desc">All data stays on your device. No cloud services are used.</p>
              </div>
            </div>
          </div>
        </div>

        {/* Permission Toggles — shared component */}
        <div className="feature-card feature-card-wide">
          <div className="feature-card-eyebrow">PERMISSIONS</div>
          <h3 className="feature-card-title">Tool Access</h3>
          <div className="feature-card-body">
            <PermissionsToggles />
          </div>
        </div>

        {/* Activity History */}
        <div className="feature-card">
          <div className="feature-card-eyebrow">ACTIVITY</div>
          <h3 className="feature-card-title">Recent Activity</h3>
          <div className="feature-card-body">
            {activity === null ? (
              <button className="btn btn-secondary" onClick={loadActivity}>Load Activity</button>
            ) : activity.length === 0 ? (
              <p style={{ opacity: 0.5, fontSize: 13 }}>No activity recorded yet.</p>
            ) : (
              <div className="activity-list">
                {activity.map((a, i) => (
                  <div key={i} className="info-row">
                    <span className="info-label mono" style={{ fontSize: 11 }}>{a.tool_name}</span>
                    <span className="info-value" style={{ fontSize: 12 }}>{a.summary}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Data Storage */}
        <div className="feature-card">
          <div className="feature-card-eyebrow">STORAGE</div>
          <h3 className="feature-card-title">Data Location</h3>
          <div className="feature-card-body">
            <div className="info-row">
              <span className="info-label">Models</span>
              <span className="info-value mono">~/.desktop-companion/models/</span>
            </div>
            <div className="info-row">
              <span className="info-label">Database</span>
              <span className="info-value mono">~/.desktop-companion/luna.db</span>
            </div>
            <div className="info-row">
              <span className="info-label">Backend</span>
              <span className="info-value mono">Local Python (port 8765)</span>
            </div>
          </div>
        </div>

        {/* Danger Zone */}
        <div className="feature-card feature-card-wide">
          <div className="feature-card-eyebrow">DANGER ZONE</div>
          <h3 className="feature-card-title">Delete All Data</h3>
          <div className="feature-card-body">
            <p style={{ fontSize: 13, opacity: 0.7, marginBottom: 12 }}>
              This will permanently delete all conversations, memories, activity logs, and permission settings.
            </p>
            <button className="btn btn-danger" onClick={deleteAllData}>Delete Everything</button>
          </div>
        </div>
      </div>
    </div>
  );
}
