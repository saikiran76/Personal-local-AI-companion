import { useState, useEffect } from 'react';
import './screens.css';

const BACKEND_URL = 'http://127.0.0.1:8765';

const MODEL_FORMATS = [
  { id: 'gguf', label: 'GGUF', desc: 'llama.cpp compatible — runs locally via CPU/GPU.', available: true },
  { id: 'onnx', label: 'ONNX Runtime', desc: 'Cross-platform ML format with hardware acceleration.', available: false, badge: 'Coming Soon' },
  { id: 'transformers', label: 'Transformers (HuggingFace)', desc: 'Full HuggingFace model support with bitsandbytes quantization.', available: false, badge: 'Coming Soon' },
  { id: 'mlx', label: 'MLX (Apple Silicon)', desc: 'Optimized for Apple M-series chips.', available: false, badge: 'Coming Soon' },
];

export default function LocalAIScreen({ config }) {
  const [modelInfo, setModelInfo] = useState(null);
  const [models, setModels] = useState([]);
  const [compute, setCompute] = useState(null);

  useEffect(() => {
    fetch(`${BACKEND_URL}/status`)
      .then((r) => r.json())
      .then((data) => {
        setModelInfo(data.model);
        setCompute(data.compute);
      })
      .catch(() => {});

    fetch(`${BACKEND_URL}/models/list`)
      .then((r) => r.json())
      .then((data) => setModels(data.models || []))
      .catch(() => {});
  }, []);

  return (
    <div className="screen-container">
      <div className="screen-header">
        <h1 className="screen-title">Local AI</h1>
        <p className="screen-subtitle">Manage your local model and compute resources</p>
      </div>

      <div className="screen-grid">
        {/* Compute Info */}
        <div className="feature-card">
          <div className="feature-card-eyebrow">COMPUTE</div>
          <h3 className="feature-card-title">Hardware</h3>
          {compute ? (
            <div className="feature-card-body">
              <div className="info-row">
                <span className="info-label">Device</span>
                <span className="info-value">{compute.device_name}</span>
              </div>
              <div className="info-row">
                <span className="info-label">VRAM</span>
                <span className="info-value">{compute.vram_mb} MB</span>
              </div>
              <div className="info-row">
                <span className="info-label">RAM</span>
                <span className="info-value">{compute.ram_mb} MB</span>
              </div>
              <div className="info-row">
                <span className="info-label">CPU Cores</span>
                <span className="info-value">{compute.cpu_cores}</span>
              </div>
              <div className="info-row">
                <span className="info-label">Tier</span>
                <span className="info-value badge">{compute.tier}</span>
              </div>
            </div>
          ) : (
            <div className="feature-card-empty">Loading...</div>
          )}
        </div>

        {/* Active Model */}
        <div className="feature-card">
          <div className="feature-card-eyebrow">MODEL</div>
          <h3 className="feature-card-title">Active Model</h3>
          {modelInfo ? (
            <div className="feature-card-body">
              <div className="info-row">
                <span className="info-label">Name</span>
                <span className="info-value">{modelInfo.name}</span>
              </div>
              <div className="info-row">
                <span className="info-label">Device</span>
                <span className="info-value">{modelInfo.device}</span>
              </div>
              <div className="info-row">
                <span className="info-label">Quantization</span>
                <span className="info-value">{modelInfo.quantization}</span>
              </div>
              <div className="info-row">
                <span className="info-label">GPU Layers</span>
                <span className="info-value">{modelInfo.n_gpu_layers}</span>
              </div>
              <div className="info-row">
                <span className="info-label">Threads</span>
                <span className="info-value">{modelInfo.n_threads}</span>
              </div>
              <div className="info-row">
                <span className="info-label">Load Time</span>
                <span className="info-value">{Math.round(modelInfo.load_time_ms)}ms</span>
              </div>
              <div className="info-row">
                <span className="info-label">Status</span>
                <span className={`info-value badge ${modelInfo.status}`}>{modelInfo.status}</span>
              </div>
            </div>
          ) : (
            <div className="feature-card-empty">No model loaded</div>
          )}
        </div>

        {/* Model Format Support */}
        <div className="feature-card feature-card-wide">
          <div className="feature-card-eyebrow">FORMATS</div>
          <h3 className="feature-card-title">Model Format Support</h3>
          <div className="feature-card-body">
            <div className="model-format-list">
              {MODEL_FORMATS.map((fmt) => (
                <div
                  key={fmt.id}
                  className={`model-format-item ${fmt.available ? 'available' : 'disabled'}`}
                >
                  <div className="model-format-info">
                    <span className="model-format-name">{fmt.label}</span>
                    <span className="model-format-desc">{fmt.desc}</span>
                  </div>
                  {fmt.available ? (
                    <span className="badge badge-active">Supported</span>
                  ) : (
                    <span className="badge badge-soon">{fmt.badge}</span>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Model Registry */}
        <div className="feature-card feature-card-wide">
          <div className="feature-card-eyebrow">REGISTRY</div>
          <h3 className="feature-card-title">Available GGUF Models</h3>
          <div className="feature-card-body">
            {models.length > 0 ? (
              <div className="model-list">
                {models.map((m, i) => (
                  <div key={i} className={`model-item ${m.name === modelInfo?.name ? 'active' : ''}`}>
                    <div className="model-item-info">
                      <span className="model-item-name">{m.name}</span>
                      <span className="model-item-meta">{m.quantization} · {m.size_mb} MB</span>
                    </div>
                    {m.name === modelInfo?.name && (
                      <span className="badge badge-active">Active</span>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="feature-card-empty">No GGUF models imported yet. Drag &amp; drop a .gguf file onto the app window to import one.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
