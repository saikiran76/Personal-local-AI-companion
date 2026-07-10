# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Voice STT server.

Produces a standalone voice_stt.exe containing faster-whisper + ctranslate2.
Spawned by the main backend.exe as a subprocess via stdio JSON-RPC.
"""

import sys
from pathlib import Path

block_cipher = None

stt_dir = Path(SPECPATH)

a = Analysis(
    [str(stt_dir / 'server.py')],
    pathex=[str(stt_dir)],
    binaries=[],
    datas=[],
    hiddenimports=[
        'faster_whisper',
        'faster_whisper.transcribe',
        'faster_whisper.tokenizer',
        'faster_whisper.feature_extractor',
        'ctranslate2',
        'tokenizers',
        'huggingface_hub',
        'onnxruntime',
        'onnxruntime.capi',
        'numpy',
        'av',
        'av.audio',
        'av.container',
    ],
    excludes=[
        'torch', 'torchvision', 'torchaudio',
        'transformers', 'bitsandbytes', 'peft',
        'llama_cpp', 'fastapi', 'uvicorn', 'starlette',
        'scipy', 'pandas', 'matplotlib',
        'PIL', 'tkinter', 'unittest', 'test',
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
    name='voice_stt',
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
