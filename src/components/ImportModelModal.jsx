import { useState, useRef, useCallback } from 'react';
import { X, CheckCircle, File, Upload, Folder } from 'lucide-react';

const BACKEND_URL = 'http://127.0.0.1:8765';

const ACCEPTED_TYPES = ['.gguf'];
const SPLIT_GGUF_RE = /-\d{4,}-of-\d{4,}\.gguf$/i;

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
    if (SPLIT_GGUF_RE.test(file.name)) {
      setError('Split GGUF shards are not supported. Select a merged .gguf file.');
      return;
    }
    setSelectedFile({
      name: file.name,
      size: file.size,
      source: 'file',
      file,
      path: null,
    });
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

  const handleSelectFile = useCallback(async () => {
    setError('');

    if (window.electronAPI?.importModel) {
      try {
        const result = await window.electronAPI.importModel();
        if (result.canceled) return;

        if (result.success && result.selected?.length > 0) {
          const selected = result.selected[0];
          if (SPLIT_GGUF_RE.test(selected.fileName)) {
            setError('Split GGUF shards are not supported. Select a merged .gguf file.');
            return;
          }
          setSelectedFile({
            name: selected.fileName,
            size: selected.sizeBytes || 0,
            source: 'native',
            path: selected.filePath,
            file: null,
          });
          return;
        }

        if (result.success) {
          setError('No model file was selected');
          return;
        }

        setError(result.error || 'Unable to select a model file');
      } catch (err) {
        console.error('Native file selection failed:', err);
        setError(err.message || 'Unable to open file picker');
      }
      return;
    }

    fileInputRef.current?.click();
  }, []);

  const handleImport = async () => {
    if (importing) return;

    if (!selectedFile) {
      await handleSelectFile();
      return;
    }

    setImporting(true);
    setError('');
    setProgress(0);

    try {
      if (selectedFile.source === 'native' && selectedFile.path && window.electronAPI?.importModel) {
        const result = await window.electronAPI.importModel(selectedFile.path);
        if (result.success && result.imported?.length > 0) {
          setImported(true);
          setProgress(100);
          onImported?.(result.imported, false);
          return;
        }
        if (result.canceled) {
          return;
        }
        throw new Error(result.error || 'Import failed');
      }

      if (!selectedFile.file) {
        throw new Error('No file payload available for upload');
      }

      const formData = new FormData();
      formData.append('file', selectedFile.file);

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
        onImported?.([response.model], response.model_loaded);
      } else {
        setError(response.error || 'Import failed');
      }
    } catch (err) {
      setError(err.message || 'Import failed. Is the backend running?');
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="import-modal-overlay" onClick={onClose}>
      <div className="import-modal" onClick={(e) => e.stopPropagation()}>
        <div className="import-modal-header">
          <h3>Import Model</h3>
          <button className="import-modal-close" onClick={onClose}>
            <X size={16} strokeWidth={2} />
          </button>
        </div>

        <div className="import-modal-body">
          {imported ? (
            <div className="import-success">
              <div className="import-success-icon">
                <CheckCircle size={32} strokeWidth={2} />
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
                      <File size={24} strokeWidth={2} />
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
                      <Upload size={32} strokeWidth={1.5} />
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
                  onClick={handleSelectFile}
                >
                  <Folder size={14} strokeWidth={2} />
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
