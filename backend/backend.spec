# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Desktop Companion backend.

Produces a single .exe that serves as both:
  - The main FastAPI backend server (default)
  - An MCP server subprocess (when called with --mcp-server <name>)

Run: pyinstaller backend.spec
Output: dist/backend.exe
"""

import sys
from pathlib import Path

block_cipher = None

backend_dir = Path(SPECPATH)

# llama_cpp needs its lib/ directory (DLLs) bundled as data
llama_cpp_path = Path(sys.prefix) / "Lib" / "site-packages" / "llama_cpp"
llama_lib_dir = llama_cpp_path / "lib"

a = Analysis(
    [str(backend_dir / 'server.py')],
    pathex=[str(backend_dir)],
    binaries=[],
    datas=[
        # Bundle llama_cpp native libraries (if present)
    ] + ([(str(llama_lib_dir), "llama_cpp/lib")] if llama_lib_dir.exists() else []),
    hiddenimports=[
        # --- App modules ---
        'app.config',
        'app.model_loader',
        'app.agent',
        'app.routes',
        'app.database',
        'app.mcp_client',
        # --- MCP servers (imported via --mcp-server flag) ---
        'mcp_servers.filesystem.server',
        'mcp_servers.notes.server',
        'mcp_servers.browser.server',
        'mcp_servers.email.server',
        'mcp_servers.reminders.server',
        # --- FastAPI / uvicorn ---
        'fastapi',
        'fastapi.middleware.cors',
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'sse_starlette',
        'pydantic',
        'starlette',
        'starlette.responses',
        'starlette.routing',
        # --- llama-cpp-python (C extension, may need special handling) ---
        'llama_cpp',
        # --- Other deps ---
        'psutil',
        'httpx',
        'httpx._transports',
        'httpx._transports.default',
        'dotenv',
        'json',
        'asyncio',
        'ssl',
        'certifi',
        'pypdf',
        'bs4',
        'beautifulsoup4',
    ],
    excludes=[
        # --- Huge deps NOT actually used at runtime ---
        'torch',
        'torchvision',
        'torchaudio',
        'transformers',
        'bitsandbytes',
        'peft',
        'langchain',
        'langchain_core',
        'langchain_community',
        'numpy',
        'scipy',
        'pandas',
        'matplotlib',
        'PIL',
        'tkinter',
        'unittest',
        'test',
        'xmlrpc',
        'pydoc',
        'pdb',
        'profile',
        'pstats',
        'lib2to3',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
