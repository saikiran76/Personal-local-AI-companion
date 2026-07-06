// Wrapper around electron-store for renderer process
// Falls back to localStorage when running outside Electron (e.g. vite dev)

const isElectron = typeof window !== 'undefined' && window.electronAPI?.store;

export const config = {
  async get(key) {
    if (isElectron) return window.electronAPI.store.get(key);
    const raw = localStorage.getItem('dc_config');
    const data = raw ? JSON.parse(raw) : {};
    return data[key];
  },

  async set(key, value) {
    if (isElectron) return window.electronAPI.store.set(key, value);
    const raw = localStorage.getItem('dc_config');
    const data = raw ? JSON.parse(raw) : {};
    data[key] = value;
    localStorage.setItem('dc_config', JSON.stringify(data));
  },

  async getAll() {
    if (isElectron) return window.electronAPI.store.getAll();
    const raw = localStorage.getItem('dc_config');
    return raw ? JSON.parse(raw) : {};
  },

  async reset() {
    if (isElectron) return window.electronAPI.store.reset();
    localStorage.removeItem('dc_config');
  },
};
