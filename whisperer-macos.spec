# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the macOS Whisperer app.

Usage:
    pyinstaller --noconfirm whisperer-macos.spec

Output:
    dist/Whisperer.app
"""

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

PROJECT_ROOT = os.path.abspath(".")
APP_ICON = os.path.join(PROJECT_ROOT, "assets", "whisperer.icns")
FASTER_WHISPER_DATAS = collect_data_files("faster_whisper", includes=["assets/*"])
FASTER_WHISPER_HIDDEN_IMPORTS = collect_submodules("faster_whisper")
ONNXRUNTIME_HIDDEN_IMPORTS = collect_submodules("onnxruntime")
RIVA_HIDDEN_IMPORTS = collect_submodules("riva")
GRPC_HIDDEN_IMPORTS = collect_submodules("grpc")


def collect_source_tree(dirname):
    entries = []
    source_root = os.path.join(PROJECT_ROOT, dirname)
    if not os.path.isdir(source_root):
        return entries
    for dirpath, dirnames, filenames in os.walk(source_root):
        dirnames[:] = [name for name in dirnames if name != "__pycache__"]
        dest = os.path.relpath(dirpath, PROJECT_ROOT)
        for filename in filenames:
            if filename.endswith((".pyc", ".pyo")):
                continue
            entries.append((os.path.join(dirpath, filename), dest))
    return entries


SOURCE_DATAS = [
    (os.path.join(PROJECT_ROOT, "main.py"), "."),
    (os.path.join(PROJECT_ROOT, "config.py"), "."),
    (os.path.join(PROJECT_ROOT, "launcher.py"), "."),
]
for source_dir in ("core", "ui", "rules", "scripts", "assets"):
    SOURCE_DATAS.extend(collect_source_tree(source_dir))
SOURCE_DATAS.extend(collect_source_tree(os.path.join("whisperer-app", "dist")))
SOURCE_DATAS.extend(FASTER_WHISPER_DATAS)


a = Analysis(
    [os.path.join(PROJECT_ROOT, "launcher.py")],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=SOURCE_DATAS,
    hiddenimports=[
        "core.audio",
        "core.audio_ducking",
        "core.context",
        "core.dictionary",
        "core.history",
        "core.hotkeys",
        "core.migrations",
        "core.modes",
        "core.native",
        "core.output",
        "core.paths",
        "core.perf",
        "core.secrets",
        "core.settings",
        "core.single_instance",
        "core.transcriber",
        "scripts.diagnostics",
        "scripts.launch_on_login",
        "ui.fonts",
        "ui.macos_glass",
        "ui.main_window",
        "ui.overlay",
        "ui.tray",
        "PyQt6",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtNetwork",
        "PyQt6.QtWidgets",
        "PyQt6.QtWebChannel",
        "PyQt6.QtWebEngineCore",
        "PyQt6.QtWebEngineWidgets",
        "faster_whisper",
        "ctranslate2",
        "huggingface_hub",
        "tokenizers",
        "onnxruntime",
        "pynput",
        "pyperclip",
        "keyring",
        "sounddevice",
        "numpy",
        "PIL",
        "pytesseract",
        "mss",
    ]
    + FASTER_WHISPER_HIDDEN_IMPORTS
    + ONNXRUNTIME_HIDDEN_IMPORTS
    + RIVA_HIDDEN_IMPORTS
    + GRPC_HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "models",
        "pytest",
        "unittest",
        "tkinter",
        "matplotlib",
        "scipy",
        "pandas",
        "sklearn",
        "pywin32",
        "win32com",
        "pythoncom",
        "keyboard",
        "pycaw",
        "nemo",
        "nemo_toolkit",
        "torch",
        "torchaudio",
        "torchvision",
        "transformers",
        "accelerate",
        "onnx",
    ],
    noarchive=False,
)


def exclude_large_files(binaries, datas):
    filtered_binaries = []
    for dest, src, typecode in binaries:
        lower = dest.lower()
        if "v8_context_snapshot" in lower:
            filtered_binaries.append((dest, src, typecode))
            continue
        if lower.endswith(".onnx") and "silero_vad" in lower:
            filtered_binaries.append((dest, src, typecode))
            continue
        if lower.endswith((".bin", ".pt", ".pth", ".safetensors", ".ckpt", ".onnx")):
            continue
        filtered_binaries.append((dest, src, typecode))

    filtered_datas = []
    for dest, src, typecode in datas:
        lower = dest.lower()
        if "v8_context_snapshot" in lower:
            filtered_datas.append((dest, src, typecode))
            continue
        if lower.endswith(".onnx") and "silero_vad" in lower:
            filtered_datas.append((dest, src, typecode))
            continue
        if lower.endswith((".bin", ".pt", ".pth", ".safetensors", ".ckpt", ".onnx")):
            continue
        filtered_datas.append((dest, src, typecode))

    return filtered_binaries, filtered_datas


a.binaries, a.datas = exclude_large_files(a.binaries, a.datas)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Whisperer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="Whisperer",
)

app = BUNDLE(
    coll,
    name="Whisperer.app",
    icon=APP_ICON if os.path.exists(APP_ICON) else None,
    bundle_identifier="com.whisperer.app",
    info_plist={
        "CFBundleDisplayName": "Whisperer",
        "CFBundleShortVersionString": "6.0.2",
        "CFBundleVersion": "6.0.2",
        "CFBundleIconFile": "whisperer.icns",
        "NSMicrophoneUsageDescription": "Whisperer records microphone audio for local dictation.",
        "NSAppleEventsUsageDescription": "Whisperer uses Apple Events to paste dictation and read active-window context.",
        "NSScreenCaptureDescription": "Whisperer can capture active-window text for optional OCR context.",
    },
)
