import { useState, Suspense } from 'react';
import { ShaderGradient, ShaderGradientCanvas } from '@shadergradient/react';

const STEPS = [
  { key: 'intro', label: 'Welcome' },
  { key: 'capabilities', label: 'Capabilities' },
  { key: 'model', label: 'Model' },
  { key: 'data', label: 'Data' },
  { key: 'ready', label: 'Ready' },
];

const MODEL_OPTIONS = [
  {
    id: 'bring',
    label: 'Bring your own model (.GGUF)',
    desc: 'Use a GGUF model file you already have on disk.',
    badge: null,
    available: true,
  },
  {
    id: 'auto',
    label: 'Auto-download recommended model',
    desc: "We'll set up a lightweight model automatically.",
    badge: 'Coming Soon',
    available: false,
  },
  {
    id: 'hybrid',
    label: 'Hybrid (local + cloud fallback)',
    desc: 'Run locally by default, with optional cloud fallback for heavy tasks.',
    badge: 'Coming Soon',
    available: false,
  },
];

const DATA_OPTIONS = [
  { id: 'default', label: 'Default location', desc: '~/.desktop-companion/ — standard user data directory.' },
  { id: 'custom', label: 'Custom location', desc: 'Choose a specific folder on your device.' },
];

function GradientBackground() {
  return (
    <div className="shader-gradient-bg">
      <ShaderGradientCanvas
        style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%' }}
        pixelDensity={1}
      >
        <Suspense fallback={null}>
          <ShaderGradient
            animate="on"
            axesHelper="off"
            brightness={0.8}
            cAzimuthAngle={270}
            cDistance={0.5}
            cPolarAngle={180}
            cameraZoom={15.1}
            color1="#73bfc4"
            color2="#ff810a"
            color3="#8da0ce"
            destination="onCanvas"
            embedMode="off"
            envPreset="city"
            format="gif"
            fov={45}
            frameRate={10}
            gizmoHelper="hide"
            grain="on"
            lightType="env"
            pixelDensity={1}
            positionX={-0.1}
            positionY={0}
            positionZ={0}
            range="disabled"
            rangeEnd={40}
            rangeStart={0}
            reflection={0.4}
            rotationX={0}
            rotationY={130}
            rotationZ={70}
            shader="defaults"
            type="sphere"
            uAmplitude={3.2}
            uDensity={0.8}
            uFrequency={5.5}
            uSpeed={0.3}
            uStrength={0.3}
            uTime={0}
            wireframe={false}
          />
        </Suspense>
      </ShaderGradientCanvas>
    </div>
  );
}

function ProgressDots({ active }) {
  return (
    <div className="step-progress">
      {STEPS.map((_, i) => (
        <div
          key={i}
          className={`step-dot ${i === active ? 'active' : ''} ${i < active ? 'completed' : ''}`}
        />
      ))}
    </div>
  );
}

export default function WelcomeScreen({ onComplete }) {
  const [step, setStep] = useState(0);
  const [model, setModel] = useState('bring');
  const [dataLoc, setDataLoc] = useState('default');

  const next = () => setStep((s) => Math.min(s + 1, STEPS.length - 1));
  const prev = () => setStep((s) => Math.max(s - 1, 0));

  if (step === 0) {
    return (
      <div className="screen">
        <GradientBackground />
        <div className="screen-content">
          <div className="welcome-logo">&#x2728;</div>
          <p className="welcome-eyebrow">introducing</p>
          <h1 className="welcome-title">Luna</h1>
          <p className="welcome-subtitle">
            Your personal AI assistant that runs entirely on your device.
            Private, fast, and always available — no cloud required.
          </p>
          <button className="btn-primary" onClick={next}>Get Started</button>
          <div className="privacy-callout" style={{ marginTop: 24 }}>
            <span style={{ fontSize: 16 }}>&#x1F512;</span>
            <span className="privacy-text">Everything runs locally on your machine. Your data never leaves your device.</span>
          </div>
        </div>
      </div>
    );
  }

  if (step === 1) {
    return (
      <div className="screen">
        <GradientBackground />
        <div className="screen-content">
          <p className="welcome-eyebrow">capabilities</p>
          <h2 className="step-title">What I can do for you</h2>
          <p className="step-desc">A glimpse of what your desktop companion can help with.</p>
          <div className="features-grid">
            {[
              { icon: '💬', label: 'Personalized Conversations', desc: 'Context-aware chat that remembers your preferences and adapts to your style.' },
              { icon: '⚡', label: 'Desktop Automation', desc: 'Organize files, manage schedules, and automate repetitive tasks.' },
              { icon: '🧠', label: 'Local AI Processing', desc: 'Powered by open-source models running directly on your hardware.' },
              { icon: '🔒', label: 'Privacy-First Design', desc: 'Your data stays on your device. No telemetry, no cloud sync.' },
            ].map((f) => (
              <div className="feature-card" key={f.label}>
                <div className="feature-icon">{f.icon}</div>
                <div className="feature-label">{f.label}</div>
                <div className="feature-desc">{f.desc}</div>
              </div>
            ))}
          </div>
          <div className="step-footer">
            <button className="btn-ghost" onClick={prev}>Back</button>
            <ProgressDots active={1} />
            <button className="btn-primary" onClick={next}>Continue</button>
          </div>
        </div>
      </div>
    );
  }

  if (step === 2) {
    return (
      <div className="screen">
        <GradientBackground />
        <div className="screen-content">
          <p className="welcome-eyebrow">configuration</p>
          <h2 className="step-title">Choose your AI model</h2>
          <p className="step-desc">Select how you'd like the assistant to run. You can change this later.</p>
          <div className="option-list">
            {MODEL_OPTIONS.map((opt) => (
              <div
                key={opt.id}
                className={`option-item ${model === opt.id ? 'selected' : ''} ${!opt.available ? 'option-disabled' : ''}`}
                onClick={() => opt.available && setModel(opt.id)}
              >
                <div className="option-radio" />
                <div className="option-content">
                  <div className="option-label">{opt.label}</div>
                  <div className="option-desc">{opt.desc}</div>
                </div>
                {opt.badge && <span className="option-badge option-badge-soon">{opt.badge}</span>}
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

  if (step === 3) {
    return (
      <div className="screen">
        <GradientBackground />
        <div className="screen-content">
          <p className="welcome-eyebrow">configuration</p>
          <h2 className="step-title">Where should I store your data?</h2>
          <p className="step-desc">All data is stored locally. Choose a location that works for you.</p>
          <div className="option-list">
            {DATA_OPTIONS.map((opt) => (
              <div
                key={opt.id}
                className={`option-item ${dataLoc === opt.id ? 'selected' : ''}`}
                onClick={() => setDataLoc(opt.id)}
              >
                <div className="option-radio" />
                <div className="option-content">
                  <div className="option-label">{opt.label}</div>
                  <div className="option-desc">{opt.desc}</div>
                </div>
              </div>
            ))}
          </div>
          <div className="privacy-callout">
            <span style={{ fontSize: 16 }}>&#x1F510;</span>
            <span className="privacy-text">All data is encrypted at rest. Conversations, memories, and settings never leave your device.</span>
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

  // Step 4: Ready
  return (
    <div className="screen">
      <GradientBackground />
      <div className="screen-content">
        <div className="welcome-logo" style={{ fontSize: 24 }}>&#x2714;</div>
        <h2 className="step-title">You're all set</h2>
        <p className="step-desc">Your desktop companion is ready. Here's a quick summary.</p>
        <div className="features-grid" style={{ gridTemplateColumns: '1fr', maxWidth: 400 }}>
          <div className="feature-card" style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <div className="feature-icon">🧠</div>
            <div style={{ textAlign: 'left' }}>
              <div className="feature-label">Model</div>
              <div className="feature-desc">
                {model === 'bring' && 'Bring your own model (.GGUF)'}
                {model === 'auto' && 'Auto-downloaded (coming soon)'}
                {model === 'hybrid' && 'Hybrid (coming soon)'}
              </div>
            </div>
          </div>
          <div className="feature-card" style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <div className="feature-icon">📁</div>
            <div style={{ textAlign: 'left' }}>
              <div className="feature-label">Data Storage</div>
              <div className="feature-desc">
                {dataLoc === 'default' ? '~/.desktop-companion/' : 'Custom location'}
              </div>
            </div>
          </div>
        </div>
        <div className="privacy-callout" style={{ marginTop: 24 }}>
          <span style={{ fontSize: 16 }}>&#x1F512;</span>
          <span className="privacy-text">You're in full control. Adjust privacy settings anytime from the dashboard.</span>
        </div>
        <button
          className="btn-primary"
          style={{ marginTop: 24 }}
          onClick={() => onComplete({ model, dataLocation: dataLoc })}
        >
          Continue to Setup
        </button>
      </div>
    </div>
  );
}
