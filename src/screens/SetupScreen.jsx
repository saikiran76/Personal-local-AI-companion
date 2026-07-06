import { useState } from 'react';

const SETUP_STEPS = [
  { key: 'username', label: 'Username' },
  { key: 'assistant', label: 'Assistant Name' },
  { key: 'language', label: 'Language' },
  { key: 'theme', label: 'Theme' },
  { key: 'model', label: 'Model' },
];

const LANGUAGES = [
  { id: 'en', label: 'English' },
  { id: 'es', label: 'Spanish' },
  { id: 'fr', label: 'French' },
  { id: 'de', label: 'German' },
  { id: 'ja', label: 'Japanese' },
  { id: 'ko', label: 'Korean' },
  { id: 'zh', label: 'Chinese' },
  { id: 'pt', label: 'Portuguese' },
  { id: 'ar', label: 'Arabic' },
  { id: 'hi', label: 'Hindi' },
];

const MODELS = [
  {
    id: 'auto',
    label: 'Phi-3 Mini (3.8B)',
    desc: 'Fast, lightweight — best for everyday tasks.',
    badge: 'Recommended',
  },
  {
    id: 'medium',
    label: 'Llama 3.1 (8B)',
    desc: 'Balanced performance and speed.',
  },
  {
    id: 'heavy',
    label: 'Llama 3.1 (70B)',
    desc: 'Most capable, requires significant RAM/VRAM.',
  },
  {
    id: 'bring',
    label: 'Bring your own',
    desc: 'Point to a local GGUF/ONNX file.',
  },
];

function ProgressDots({ active }) {
  return (
    <div className="step-progress">
      {SETUP_STEPS.map((_, i) => (
        <div
          key={i}
          className={`step-dot ${i === active ? 'active' : ''} ${i < active ? 'completed' : ''}`}
        />
      ))}
    </div>
  );
}

export default function SetupScreen({ onComplete }) {
  const [step, setStep] = useState(0);
  const [username, setUsername] = useState('');
  const [assistantName, setAssistantName] = useState('Companion');
  const [language, setLanguage] = useState('en');
  const [theme, setTheme] = useState('light');
  const [model, setModel] = useState('auto');

  const next = () => setStep((s) => Math.min(s + 1, SETUP_STEPS.length - 1));
  const prev = () => setStep((s) => Math.max(s - 1, 0));

  const handleFinish = () => {
    onComplete({ userName: username, assistantName, language, theme, model });
  };

  // Step 0: Username
  if (step === 0) {
    return (
      <div className="screen">
        <div className="screen-content">
          <p className="welcome-eyebrow">step 1 of 5</p>
          <h2 className="step-title">What should I call you?</h2>
          <p className="step-desc">This is how the assistant will address you in conversations.</p>
          <div className="form-group">
            <label className="form-label">Your name</label>
            <input
              className="text-input"
              type="text"
              placeholder="e.g. Alex"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
            />
            <p className="form-hint">You can change this later in settings.</p>
          </div>
          <div className="step-footer">
            <button className="btn-ghost" onClick={prev} disabled>Back</button>
            <ProgressDots active={0} />
            <button className="btn-primary" onClick={next} disabled={!username.trim()}>
              Continue
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Step 1: Assistant Name
  if (step === 1) {
    return (
      <div className="screen">
        <div className="screen-content">
          <p className="welcome-eyebrow">step 2 of 5</p>
          <h2 className="step-title">Name your assistant</h2>
          <p className="step-desc">Give your AI companion a name that feels right.</p>
          <div className="form-group">
            <label className="form-label">Assistant name</label>
            <input
              className="text-input"
              type="text"
              placeholder="e.g. Companion, Nova, Atlas"
              value={assistantName}
              onChange={(e) => setAssistantName(e.target.value)}
              autoFocus
            />
            <p className="form-hint">This name appears in the sidebar and conversations.</p>
          </div>
          <div className="step-footer">
            <button className="btn-ghost" onClick={prev}>Back</button>
            <ProgressDots active={1} />
            <button className="btn-primary" onClick={next} disabled={!assistantName.trim()}>
              Continue
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Step 2: Language
  if (step === 2) {
    return (
      <div className="screen">
        <div className="screen-content">
          <p className="welcome-eyebrow">step 3 of 5</p>
          <h2 className="step-title">Preferred language</h2>
          <p className="step-desc">Choose the language for conversations and interface text.</p>
          <div className="option-list" style={{ maxHeight: 320, overflowY: 'auto' }}>
            {LANGUAGES.map((lang) => (
              <div
                key={lang.id}
                className={`option-item ${language === lang.id ? 'selected' : ''}`}
                onClick={() => setLanguage(lang.id)}
              >
                <div className="option-radio" />
                <div className="option-content">
                  <div className="option-label">{lang.label}</div>
                </div>
              </div>
            ))}
          </div>
          <div className="step-footer">
            <button className="btn-ghost" onClick={prev}>Back</button>
            <ProgressDots active={2} />
            <button className="btn-primary" onClick={next}>Continue</button>
          </div>
        </div>
      </div>
    );
  }

  // Step 3: Theme
  if (step === 3) {
    return (
      <div className="screen">
        <div className="screen-content">
          <p className="welcome-eyebrow">step 4 of 5</p>
          <h2 className="step-title">Choose your theme</h2>
          <p className="step-desc">Pick a look that suits your workflow. You can switch anytime.</p>
          <div className="theme-grid">
            <div
              className={`theme-card ${theme === 'light' ? 'selected' : ''}`}
              onClick={() => setTheme('light')}
            >
              <div className="theme-preview theme-preview-light">
                <div className="tp-bar" />
                <div className="tp-body">
                  <div className="tp-line" style={{ width: '60%' }} />
                  <div className="tp-line" style={{ width: '40%' }} />
                </div>
              </div>
              <span className="theme-label">Light</span>
            </div>
            <div
              className={`theme-card ${theme === 'dark' ? 'selected' : ''}`}
              onClick={() => setTheme('dark')}
            >
              <div className="theme-preview theme-preview-dark">
                <div className="tp-bar" />
                <div className="tp-body">
                  <div className="tp-line" style={{ width: '60%' }} />
                  <div className="tp-line" style={{ width: '40%' }} />
                </div>
              </div>
              <span className="theme-label">Dark</span>
            </div>
          </div>
          <div className="step-footer">
            <button className="btn-ghost" onClick={prev}>Back</button>
            <ProgressDots active={3} />
            <button className="btn-primary" onClick={next}>Continue</button>
          </div>
        </div>
      </div>
    );
  }

  // Step 4: Model Selection
  return (
    <div className="screen">
      <div className="screen-content">
        <p className="welcome-eyebrow">step 5 of 5</p>
        <h2 className="step-title">Select your AI model</h2>
        <p className="step-desc">Choose the model that matches your hardware. Larger models are more capable but need more resources.</p>
        <div className="option-list">
          {MODELS.map((opt) => (
            <div
              key={opt.id}
              className={`option-item ${model === opt.id ? 'selected' : ''}`}
              onClick={() => setModel(opt.id)}
            >
              <div className="option-radio" />
              <div className="option-content">
                <div className="option-label">{opt.label}</div>
                <div className="option-desc">{opt.desc}</div>
              </div>
              {opt.badge && <span className="option-badge">{opt.badge}</span>}
            </div>
          ))}
        </div>
        <div className="step-footer">
          <button className="btn-ghost" onClick={prev}>Back</button>
          <ProgressDots active={4} />
          <button className="btn-primary" onClick={handleFinish}>
            Finish Setup
          </button>
        </div>
      </div>
    </div>
  );
}
