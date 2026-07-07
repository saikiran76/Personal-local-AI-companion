import { useState } from 'react';
import './screens.css';

export default function PrivacyScreen({ config }) {
  const [settings, setSettings] = useState({
    localProcessing: true,
    cloudSync: false,
    analytics: false,
    encryption: true,
    autoDelete: false,
  });

  const toggle = (key) => {
    setSettings({ ...settings, [key]: !settings[key] });
  };

  const privacyItems = [
    { key: 'localProcessing', title: 'Local Processing', desc: 'All AI inference runs on your device' },
    { key: 'encryption', title: 'Encryption at Rest', desc: 'Encrypt stored data and conversation history' },
    { key: 'cloudSync', title: 'Cloud Sync', desc: 'Sync data across devices (requires account)' },
    { key: 'analytics', title: 'Usage Analytics', desc: 'Help improve the app with anonymous usage data' },
    { key: 'autoDelete', title: 'Auto-Delete History', desc: 'Automatically delete conversations after 30 days' },
  ];

  return (
    <div className="screen-container">
      <div className="screen-header">
        <h1 className="screen-title">Privacy</h1>
        <p className="screen-subtitle">Your data never leaves your device — full transparency</p>
      </div>

      <div className="screen-grid">
        {/* Privacy Shield */}
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
                <span className="privacy-score-value">90%</span>
              </div>
              <div className="privacy-score-details">
                <div className="privacy-score-label">Excellent</div>
                <p className="privacy-score-desc">Your data stays on your device. No cloud services are enabled.</p>
              </div>
            </div>
          </div>
        </div>

        {/* Settings */}
        <div className="feature-card feature-card-wide">
          <div className="feature-card-eyebrow">SETTINGS</div>
          <h3 className="feature-card-title">Privacy Controls</h3>
          <div className="feature-card-body">
            <div className="privacy-settings">
              {privacyItems.map((item) => (
                <div key={item.key} className="privacy-setting">
                  <div className="privacy-setting-info">
                    <span className="privacy-setting-title">{item.title}</span>
                    <span className="privacy-setting-desc">{item.desc}</span>
                  </div>
                  <button
                    className={`toggle-switch ${settings[item.key] ? 'active' : ''}`}
                    onClick={() => toggle(item.key)}
                  >
                    <span className="toggle-knob" />
                  </button>
                </div>
              ))}
            </div>
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
              <span className="info-label">Config</span>
              <span className="info-value mono">~/.desktop-companion/config.json</span>
            </div>
            <div className="info-row">
              <span className="info-label">Backend</span>
              <span className="info-value mono">Local Python (port 8765)</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
