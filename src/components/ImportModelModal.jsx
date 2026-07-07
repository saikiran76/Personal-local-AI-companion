import { useState, useRef, useCallback } from 'react';

const BACKEND_URL = 'http://127.0.0.1:8765';

const ACCEPTED_TYPES = ['.gguf'];

function formatSize(mb) {
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${mb} MB`;
}

export default function ImportModelModal({ onClose, onImported }) {
  const [isDragging, setIsDragging] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);
  const [importing, setImporting] = useState(false);
  const [imported, setImported] = useState(false);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState(0);
  const fileInputRef = useRef(null);

  const isValidFile = (file) => {
    if (!file) return false;
    const name = file.name.toLowerCase();
    return ACCEPTED_TYPES.some((ext) => name.endsWith(ext));
  };

  const handleFile = useCallback((file) => {
    setError('');
    if (!isValidFile(file)) {
      setError('Please select a .gguf model file');
      return;
    }
    setSelectedFile(file);
  }, []);

  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);

    const files = e.dataTransfer?.files;
    if (files && files.length > 0) {
      handleFile(files[0]);
    }
  }, [handleFile]);

  const handleInputChange = useCallback((e) => {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
  }, [handleFile]);

  const handleImport = async () => {
    if (!selectedFile || importing) return;

    setImporting(true);
    setError('');
    setProgress(0);

    // Try Electron native dialog path first (if available)
    if (window.electronAPI?.importModel) {
      try {
        const result = await window.electronAPI.importModel();
        if (result.canceled) {
          setImporting(false);
          return;
        }
        if (result.success && result.imported?.length > 0) {
          setImported(true);
          setProgress(100);
          onImported?.(result.imported);
          return;
        }
      } catch (err) {
        console.error('Electron import failed, falling back to HTTP:', err);
      }
    }

    // Fallback: upload via HTTP to backend
    try {
      const formData = new FormData();
      formData.append('file', selectedFile);

      const xhr = new XMLHttpRequest();
      xhr.open('POST', `${BACKEND_URL}/models/import`);

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          setProgress(Math.round((e.loaded / e.total) * 100));
        }
      };

      const response = await new Promise((resolve, reject) => {
        xhr.onload = () => {
          try {
            resolve(JSON.parse(xhr.responseText));
          } catch {
            reject(new Error('Invalid response from backend'));
          }
        };
        xhr.onerror = () => reject(new Error('Failed to connect to backend'));
        xhr.send(formData);
      });

      if (response.success) {
        setImported(true);
        setProgress(100);
        onImported?.([response.model]);
      } else {
        setError(response.error || 'Import failed');
      }
    } catch (err) {
      setError(err.message || 'Import failed. Is the backend running?');
    } finally {
      setImporting(false);
    }
  };

  const handleOpenNative = async () => {
    if (window.electronAPI?.importModel) {
      try {
        setImporting(true);
        const result = await window.electronAPI.importModel();
        if (result.canceled) {
          setImporting(false);
          return;
        }
        if (result.success && result.imported?.length > 0) {
          setImported(true);
          setProgress(100);
          onImported?.(result.imported);
        }
      } catch (err) {
        setError(err.message);
      } finally {
        setImporting(false);
      }
    } else {
      fileInputRef.current?.click();
    }
  };

  return (
    <div className="import-modal-overlay" onClick={onClose}>
      <div className="import-modal" onClick={(e) => e.stopPropagation()}>
        <div className="import-modal-header">
          <h3>Import Model</h3>
          <button className="import-modal-close" onClick={onClose}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>

        <div className="import-modal-body">
          {imported ? (
            <div className="import-success">
              <div className="import-success-icon">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>
                </svg>
              </div>
              <p className="import-success-text">Model imported successfully</p>
              <p className="import-success-detail">{selectedFile?.name}</p>
              <button className="import-btn import-btn-primary" onClick={onClose} style={{ marginTop: 16 }}>
                Done
              </button>
            </div>
          ) : (
            <>
              {/* Drop zone */}
              <div
                className={`import-dropzone ${isDragging ? 'import-dropzone-active' : ''} ${selectedFile ? 'import-dropzone-selected' : ''}`}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                onClick={() => !selectedFile && fileInputRef.current?.click()}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".gguf"
                  onChange={handleInputChange}
                  style={{ display: 'none' }}
                />

                {selectedFile ? (
                  <div className="import-file-info">
                    <div className="import-file-icon">
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>
                      </svg>
                    </div>
                    <div className="import-file-name">{selectedFile.name}</div>
                    <div className="import-file-size">{formatSize(Math.round(selectedFile.size / (1024 * 1024)))}</div>
                    <button
                      className="import-btn import-btn-ghost"
                      onClick={(e) => { e.stopPropagation(); setSelectedFile(null); setError(''); }}
                    >
                      Choose different file
                    </button>
                  </div>
                ) : (
                  <div className="import-dropzone-content">
                    <div className="import-dropzone-icon">
                      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
                      </svg>
                    </div>
                    <p className="import-dropzone-title">
                      {isDragging ? 'Drop your model here' : 'Drag & drop a .gguf model'}
                    </p>
                    <p className="import-dropzone-subtitle">or click to browse files</p>
                  </div>
                )}
              </div>

              {/* Error */}
              {error && (
                <div className="import-error">{error}</div>
              )}

              {/* Progress bar */}
              {importing && progress > 0 && (
                <div className="import-progress">
                  <div className="import-progress-bar" style={{ width: `${progress}%` }} />
                  <span className="import-progress-text">{progress}%</span>
                </div>
              )}

              {/* Actions */}
              <div className="import-actions">
                <button className="import-btn import-btn-ghost" onClick={onClose}>
                  Cancel
                </button>
                <button
                  className="import-btn import-btn-native"
                  onClick={handleOpenNative}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                  </svg>
                  Browse files
                </button>
                <button
                  className="import-btn import-btn-primary"
                  disabled={!selectedFile || importing}
                  onClick={handleImport}
                >
                  {importing ? 'Importing...' : 'Import model'}
                </button>
              </div>

              {/* Hint */}
              <p className="import-hint">
                Models are saved to the hidden app directory automatically.
                Supported formats: GGUF (llama.cpp compatible)
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
