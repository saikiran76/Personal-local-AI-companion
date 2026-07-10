import { useState, useEffect } from 'react';

const BACKEND_URL = 'http://127.0.0.1:8765';

const SCOPE_LABELS = {
  files: { title: 'Files', desc: 'Read, write, and organize files on your device' },
  notes: { title: 'Notes', desc: 'Create and manage your notes' },
  browser: { title: 'Browser', desc: 'Open websites and search the web' },
  email: { title: 'Email', desc: 'Draft and send emails' },
  reminders: { title: 'Reminders', desc: 'Create and manage reminders' },
};

export default function PermissionsToggles({ onPermissionChange }) {
  const [permissions, setPermissions] = useState({});

  useEffect(() => {
    fetch(`${BACKEND_URL}/permissions`)
      .then((r) => r.json())
      .then((data) => {
        const map = {};
        for (const p of data.permissions || []) {
          map[p.scope] = !!p.granted;
        }
        setPermissions(map);
      })
      .catch(() => {});
  }, []);

  const toggle = async (scope) => {
    const newValue = !permissions[scope];
    setPermissions((prev) => ({ ...prev, [scope]: newValue }));
    try {
      await fetch(`${BACKEND_URL}/permissions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope, granted: newValue }),
      });
      onPermissionChange?.(scope, newValue);
    } catch {
      // Revert on failure
      setPermissions((prev) => ({ ...prev, [scope]: !newValue }));
    }
  };

  return (
    <div className="permissions-toggles">
      {Object.entries(SCOPE_LABELS).map(([scope, { title, desc }]) => (
        <div key={scope} className="privacy-setting">
          <div className="privacy-setting-info">
            <span className="privacy-setting-title">{title}</span>
            <span className="privacy-setting-desc">{desc}</span>
          </div>
          <button
            className={`toggle-switch ${permissions[scope] ? 'active' : ''}`}
            onClick={() => toggle(scope)}
          >
            <span className="toggle-knob" />
          </button>
        </div>
      ))}
    </div>
  );
}
