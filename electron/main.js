const { app, BrowserWindow, ipcMain, screen, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn, execSync } = require('child_process');
const net = require('net');

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

// --- Port & Zombie Detection ---
function isPortInUse(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once('error', () => resolve(true));
    server.once('listening', () => {
      server.close(() => resolve(false));
    });
    server.listen(port, '127.0.0.1');
  });
}

function killZombieProcesses() {
  // Kill any orphaned Python processes on our port (Windows)
  if (process.platform === 'win32') {
    try {
      // Find PIDs using port 8765 via netstat
      const output = execSync('netstat -ano | findstr :8765 | findstr LISTENING', {
        encoding: 'utf-8',
        timeout: 3000,
      }).trim();

      if (output) {
        const pids = new Set();
        for (const line of output.split('\n')) {
          const parts = line.trim().split(/\s+/);
          const pid = parts[parts.length - 1];
          if (pid && pid !== '0') pids.add(pid);
        }

        for (const pid of pids) {
          console.log(`[main] Killing zombie process on port ${BACKEND_PORT}: PID ${pid}`);
          try {
            execSync(`taskkill /F /T /PID ${pid}`, { timeout: 5000 });
          } catch {
            // Process may already be dead
          }
        }
      }
    } catch {
      // netstat found nothing — port is free
    }
  }
}

// --- Python Backend Management ---
async function startPythonBackend() {
  if (pythonProcess) return;

  // Kill any zombie processes holding the port
  killZombieProcesses();

  // Wait briefly for port to release
  await new Promise((r) => setTimeout(r, 300));

  // Check if port is still in use
  const inUse = await isPortInUse(BACKEND_PORT);
  if (inUse) {
    console.error(`[main] Port ${BACKEND_PORT} still in use after zombie cleanup`);
    // One more aggressive attempt
    killZombieProcesses();
    await new Promise((r) => setTimeout(r, 500));
  }

  const backendDir = path.join(__dirname, '..', 'backend');
  const isDev = !app.isPackaged;

  const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';

  console.log('[main] Starting Python backend...');

  pythonProcess = spawn(pythonCmd, ['-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT), '--log-level', 'info'], {
    cwd: backendDir,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      BACKEND_PORT: String(BACKEND_PORT),
    },
    // On Windows, create a process group so we can kill the tree
    detached: process.platform !== 'win32',
  });

  console.log(`[main] Python backend PID: ${pythonProcess.pid}`);

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
    const pid = pythonProcess.pid;
    console.log(`[main] Stopping Python backend (PID: ${pid})...`);

    if (process.platform === 'win32') {
      // Windows: force-kill the entire process tree
      try {
        execSync(`taskkill /F /T /PID ${pid}`, { timeout: 5000 });
        console.log('[main] Killed process tree via taskkill');
      } catch (err) {
        console.warn('[main] taskkill failed, trying SIGTERM:', err.message);
        try { pythonProcess.kill('SIGTERM'); } catch {}
      }
    } else {
      // Unix: SIGTERM first, then SIGKILL after timeout
      try {
        pythonProcess.kill('SIGTERM');
      } catch {}
      setTimeout(() => {
        try { pythonProcess?.kill('SIGKILL'); } catch {}
      }, 3000);
    }

    pythonProcess = null;
  }

  // Also kill any remaining orphans on the port
  killZombieProcesses();
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

// Model import - either select a file or import/copy it into the models directory
ipcMain.handle('model:import', async (_event, filePath) => {
  if (filePath) {
    const modelsDir = path.join(app.getPath('home'), '.desktop-companion', 'models');
    fs.mkdirSync(modelsDir, { recursive: true });

    const fileName = path.basename(filePath);
    const destPath = path.join(modelsDir, fileName);

    if (fs.existsSync(destPath)) {
      return {
        success: true,
        imported: [{ fileName, destPath, skipped: true, reason: 'already exists' }],
      };
    }

    try {
      fs.copyFileSync(filePath, destPath);
      const stats = fs.statSync(destPath);
      return {
        success: true,
        imported: [{ fileName, destPath, skipped: false, sizeBytes: stats.size }],
      };
    } catch (err) {
      return {
        success: false,
        error: err.message,
        imported: [{ fileName, error: err.message }],
      };
    }
  }

  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Import GGUF Model',
    buttonLabel: 'Select Model',
    filters: [
      { name: 'GGUF Model', extensions: ['gguf'] },
      { name: 'All Files', extensions: ['*'] },
    ],
    properties: ['openFile'],
  });

  if (result.canceled || !result.filePaths.length) {
    return { success: false, canceled: true };
  }

  const selected = result.filePaths.map((srcPath) => {
    const stats = fs.statSync(srcPath);
    return {
      fileName: path.basename(srcPath),
      filePath: srcPath,
      sizeBytes: stats.size,
    };
  });

  return { success: true, selected };
});

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
