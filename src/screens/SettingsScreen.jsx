import { useState } from 'react';
import PermissionsToggles from '../components/PermissionsToggles';
import './screens.css';

export default function SettingsScreen({ config, onReset }) {
  const [settings, setSettings] = useState({
    userName: config?.userName || '',
    assistantName: config?.assistantName || '',
    theme: config?.theme || 'dark',
    language: config?.language || 'en',
  });

  const update = (key, value) => {
    setSettings({ ...settings, [key]: value });
  };

  return (
    <div className="screen-container">
      <div className="screen-header">
        <h1 className="screen-title">Settings</h1>
        <p className="screen-subtitle">Configure your companion app</p>
      </div>

      <div className="screen-grid">
        {/* Profile */}
        <div className="feature-card">
          <div className="feature-card-eyebrow">PROFILE</div>
          <h3 className="feature-card-title">Your Info</h3>
          <div className="feature-card-body">
            <div className="form-group">
              <label className="form-label">Your Name</label>
              <input
                type="text"
                className="text-input"
                value={settings.userName}
                onChange={(e) => update('userName', e.target.value)}
                placeholder="Enter your name"
              />
            </div>
            <div className="form-group">
              <label className="form-label">Assistant Name</label>
              <input
                type="text"
                className="text-input"
                value={settings.assistantName}
                onChange={(e) => update('assistantName', e.target.value)}
                placeholder="Enter assistant name"
              />
            </div>
          </div>
        </div>

        {/* Appearance */}
        <div className="feature-card">
          <div className="feature-card-eyebrow">APPEARANCE</div>
          <h3 className="feature-card-title">Theme</h3>
          <div className="feature-card-body">
            <div className="theme-options">
              <button
                className={`theme-option ${settings.theme === 'light' ? 'active' : ''}`}
                onClick={() => update('theme', 'light')}
              >
                <div className="theme-preview theme-light">
                  <div className="theme-preview-bar" />
                  <div className="theme-preview-content" />
                </div>
                <span>Light</span>
              </button>
              <button
                className={`theme-option ${settings.theme === 'dark' ? 'active' : ''}`}
                onClick={() => update('theme', 'dark')}
              >
                <div className="theme-preview theme-dark-preview">
                  <div className="theme-preview-bar dark" />
                  <div className="theme-preview-content dark" />
                </div>
                <span>Dark</span>
              </button>
            </div>
          </div>
        </div>

        {/* Language */}
        <div className="feature-card">
          <div className="feature-card-eyebrow">LANGUAGE</div>
          <h3 className="feature-card-title">Language</h3>
          <div className="feature-card-body">
            <select
              className="text-input"
              value={settings.language}
              onChange={(e) => update('language', e.target.value)}
            >
              <option value="en">English</option>
              <option value="es">Espa&#241;ol</option>
              <option value="fr">Fran&#231;ais</option>
              <option value="de">Deutsch</option>
              <option value="ja">Japanese</option>
              <option value="zh">Chinese</option>
            </select>
          </div>
        </div>

        {/* Permissions — shared component */}
        <div className="feature-card feature-card-wide">
          <div className="feature-card-eyebrow">PERMISSIONS</div>
          <h3 className="feature-card-title">Tool Access</h3>
          <div className="feature-card-body">
            <PermissionsToggles />
          </div>
        </div>

        {/* Danger Zone */}
        <div className="feature-card feature-card-danger">
          <div className="feature-card-eyebrow">DANGER ZONE</div>
          <h3 className="feature-card-title">Reset App</h3>
          <div className="feature-card-body">
            <p className="feature-card-desc">Reset all settings and start over. This will not delete your model files.</p>
            <button className="btn-danger" onClick={onReset}>Reset Everything</button>
          </div>
        </div>
      </div>
    </div>
  );
}
