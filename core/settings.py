"""Persistent app settings."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from core.paths import get_app_data_dir

IS_MAC = sys.platform == "darwin"
DEFAULT_LOCAL_MODEL = "deepdml/faster-whisper-large-v3-turbo-ct2" if IS_MAC else "nvidia/parakeet-unified-en-0.6b"
DEFAULT_DICTATION_HOTKEY = "ctrl+cmd" if IS_MAC else "ctrl+left windows"


DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 1,
    "startup": {
        "auto_start_engine": True,
        "launch_on_login": False,
        "default_model": DEFAULT_LOCAL_MODEL,
        "gpu_device": "auto",
    },
    "dictation": {
        "restore_clipboard_after_paste": False,
        "vocabulary_prompt_limit": 80,
    },
    "performance": {
        "engine_preload": "app_start",
        "warm_microphone_stream": True,
        "context_mode": "fast",
        "paste_delay_ms": 30,
        "paste_delay_overrides": {},
        "paste_fast_path_enabled": True,
        "paste_fast_all_apps": True,
        "paste_fast_delay_ms": 12,
        "paste_fast_apps": [
            "codex",
            "textedit",
            "notes",
            "safari",
            "chrome",
            "arc",
            "notion",
            "slack",
            "cursor",
            "code",
            "xcode",
            "pages",
        ],
        "silence_trim_enabled": True,
        "streaming_stt_enabled": True,
        "streaming_adaptive_finalize_enabled": True,
        "streaming_finalize_wait_ms": 450,
        "streaming_fast_finalize_wait_ms": 220,
        "streaming_tail_capture_ms": 90,
        "streaming_audio_chunk_ms": 32,
    },
    "audio": {
        "ducking_enabled": False,
        "ducking_percent": 75,
        "input_device": None,
        "input_device_name": None,
        "input_channel": 0,
        "input_channel_auto": True,
    },
    "sound": {
        "playback_when_recording": "lower",
        "effects_enabled": True,
        "effects_volume": 80,
        "auto_gain": True,
        "silence_removal": False,
        "dynamic_normalization": False,
    },
    "ui": {
        "theme": "sun",
        "accent": "moss",
        "density": "comfortable",
    },
    "overlay": {
        "position": None,
        "opacity": 0.85,
        "blur_radius": 7,
    },
    "recording_window": {
        "style": "mini",
        "always_show_mini": True,
        "always_close": True,
    },
    "history": {
        "keep_recordings_for": "forever",
    },
    "privacy": {
        "store_audio_history": False,
        "retain_history": True,
        "capture_ocr_context": True,
    },
    "llm": {
        "ollama_url": "http://localhost:11434",
        "openai_compat_url": "http://localhost:8000",
    },
    "stt": {
        "openai_compat_url": "http://localhost:8000/v1/audio/transcriptions",
        "nvidia_nim_url": "http://localhost:9000/v1/audio/transcriptions",
    },
    "shortcuts": {
        "dictation": DEFAULT_DICTATION_HOTKEY,
        "toggle_recording": None,
        "cancel": "escape",
        "mode_next": "ctrl+alt+right",
        "mode_prev": "ctrl+alt+left",
        "open_history": None,
        "repeat_last": None,
        "push_to_talk": None,
        "mouse_shortcut": None,
    },
    "paste": {
        "method": "clipboard_paste",
        "restore_clipboard": False,
        "auto_send_enter": False,
        "per_app_overrides": {},
    },
    "onboarding": {
        "complete": True,
    },
}


def get_settings_path() -> str:
    return os.path.join(get_app_data_dir(), "settings.json")


def _merge_defaults(value: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    for key, item in value.items():
        if isinstance(item, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_defaults(item, merged[key])
        else:
            merged[key] = item
    return merged


def load_settings() -> dict[str, Any]:
    path = get_settings_path()
    if not os.path.exists(path):
        save_settings(DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS)

    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_SETTINGS)

    if not isinstance(loaded, dict):
        return dict(DEFAULT_SETTINGS)
    return _merge_defaults(loaded, DEFAULT_SETTINGS)


def save_settings(settings: dict[str, Any]):
    path = get_settings_path()
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.replace(tmp_path, path)
