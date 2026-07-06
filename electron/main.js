const { app, BrowserWindow, ipcMain, screen } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

let store;
let mainWindow;
let pythonProcess = null;
const BACKEND_PORT = 8765;

async function initializeStore() {
  const { default: Store } = await import('electron-store');
  store = new Store({
    defaults: {
      onboardingComplete: false,
      setupComplete: false,
      theme: 'light',
      userName: '',
      assistantName: 'Companion',
      language: 'en',
      model: 'auto',
      ai_preference: 'local',
      dataLocation: 'default',
    },
  });
}

// --- Python Backend Management ---
function startPythonBackend() {
  if (pythonProcess) return;

  const backendDir = path.join(__dirname, '..', 'backend');
  const isDev = !app.isPackaged;

  // Try to find uv or python
  const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';

  console.log('[main] Starting Python backend...');

  pythonProcess = spawn(pythonCmd, ['-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT), '--log-level', 'info'], {
    cwd: backendDir,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      BACKEND_PORT: String(BACKEND_PORT),
    },
  });

  pythonProcess.stdout?.on('data', (data) => {
    const msg = data.toString().trim();
    if (msg) console.log(`[python] ${msg}`);
  });

  pythonProcess.stderr?.on('data', (data) => {
    const msg = data.toString().trim();
    if (msg) console.log(`[python] ${msg}`);
  });

  pythonProcess.on('close', (code) => {
    console.log(`[main] Python backend exited with code ${code}`);
    pythonProcess = null;
  });

  pythonProcess.on('error', (err) => {
    console.error('[main] Failed to start Python backend:', err.message);
    pythonProcess = null;
  });
}

function stopPythonBackend() {
  if (pythonProcess) {
    console.log('[main] Stopping Python backend...');
    pythonProcess.kill('SIGTERM');
    pythonProcess = null;
  }
}

function createWindow() {
  const { width: screenW, height: screenH } = screen.getPrimaryDisplay().workAreaSize;
  const winW = Math.min(1200, screenW);
  const winH = Math.min(800, screenH);

  mainWindow = new BrowserWindow({
    width: winW,
    height: winH,
    minWidth: 800,
    minHeight: 600,
    frame: false,
    titleBarStyle: 'hidden',
    backgroundColor: store?.get('theme') === 'dark' ? '#0a0a0a' : '#fafafa',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    show: false,
  });

  const isDev = !app.isPackaged;
  if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }

  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.on('closed', () => {
    mainWindow = null;
    stopPythonBackend();
  });
}

// --- IPC Handlers ---
ipcMain.on('window:minimize', () => mainWindow?.minimize());
ipcMain.on('window:maximize', () => {
  if (mainWindow?.isMaximized()) mainWindow.unmaximize();
  else mainWindow?.maximize();
});
ipcMain.on('window:close', () => mainWindow?.close());

ipcMain.handle('store:get', (_event, key) => store.get(key));
ipcMain.handle('store:set', (_event, key, value) => store.set(key, value));
ipcMain.handle('store:getAll', () => store.store);
ipcMain.handle('store:reset', () => {
  store.clear();
  return store.store;
});

// Backend lifecycle
ipcMain.on('backend:start', () => startPythonBackend());
ipcMain.on('backend:stop', () => stopPythonBackend());
ipcMain.handle('backend:status', () => ({
  running: pythonProcess !== null,
  port: BACKEND_PORT,
}));

app.whenReady().then(async () => {
  await initializeStore();
  createWindow();

  // Auto-start backend when app launches (if setup is complete)
  if (store.get('setupComplete')) {
    startPythonBackend();
  }
});

app.on('window-all-closed', () => {
  stopPythonBackend();
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

app.on('before-quit', () => {
  stopPythonBackend();
});
