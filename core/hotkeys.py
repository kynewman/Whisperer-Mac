"""Global hotkey helpers with a macOS backend that supports modifier holds."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable


IS_MAC = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"

try:
    import keyboard as _keyboard
except Exception:
    _keyboard = None

try:
    from pynput import keyboard as _pynput_keyboard
except Exception:
    _pynput_keyboard = None


_pressed: set[str] = set()
_bindings: list[dict[str, object]] = []
_listener = None
_lock = threading.RLock()


def _canonical_name(name: str | None) -> str:
    key = (name or "").strip().lower()
    lookup = {
        "control": "ctrl",
        "cmd": "cmd",
        "command": "cmd",
        "meta": "cmd" if IS_MAC else "left windows",
        "win": "cmd" if IS_MAC else "left windows",
        "windows": "cmd" if IS_MAC else "left windows",
        "left windows": "cmd" if IS_MAC else "left windows",
        "right windows": "cmd" if IS_MAC else "right windows",
        "option": "alt",
        "menu": "alt",
        "esc": "escape",
        "return": "enter",
        "arrowleft": "left",
        "arrowright": "right",
        "arrowup": "up",
        "arrowdown": "down",
    }
    return lookup.get(key, key)


def normalize_hotkey(hotkey: str | None) -> str | None:
    if not hotkey:
        return hotkey
    parts = [_canonical_name(part) for part in str(hotkey).split("+") if part.strip()]
    return "+".join(part for part in parts if part)


def _parts(hotkey: str | None) -> set[str]:
    normalized = normalize_hotkey(hotkey)
    if not normalized:
        return set()
    return {part.strip() for part in normalized.split("+") if part.strip()}


def _key_to_name(key) -> str:
    if _pynput_keyboard is None:
        return ""
    special = {
        _pynput_keyboard.Key.ctrl: "ctrl",
        _pynput_keyboard.Key.ctrl_l: "ctrl",
        _pynput_keyboard.Key.ctrl_r: "ctrl",
        _pynput_keyboard.Key.cmd: "cmd",
        _pynput_keyboard.Key.cmd_l: "cmd",
        _pynput_keyboard.Key.cmd_r: "cmd",
        _pynput_keyboard.Key.alt: "alt",
        _pynput_keyboard.Key.alt_l: "alt",
        _pynput_keyboard.Key.alt_r: "alt",
        _pynput_keyboard.Key.shift: "shift",
        _pynput_keyboard.Key.shift_l: "shift",
        _pynput_keyboard.Key.shift_r: "shift",
        _pynput_keyboard.Key.esc: "escape",
        _pynput_keyboard.Key.enter: "enter",
        _pynput_keyboard.Key.space: "space",
        _pynput_keyboard.Key.left: "left",
        _pynput_keyboard.Key.right: "right",
        _pynput_keyboard.Key.up: "up",
        _pynput_keyboard.Key.down: "down",
        _pynput_keyboard.Key.tab: "tab",
        _pynput_keyboard.Key.backspace: "backspace",
        _pynput_keyboard.Key.delete: "delete",
    }
    if key in special:
        return special[key]
    char = getattr(key, "char", None)
    if char:
        return char.lower()
    return ""


def _dispatch_bindings() -> None:
    callbacks: list[Callable[[], None]] = []
    with _lock:
        for binding in _bindings:
            parts = binding["parts"]
            active = bool(binding.get("active"))
            pressed = isinstance(parts, set) and parts.issubset(_pressed)
            if pressed and not active:
                binding["active"] = True
                callback = binding.get("callback")
                if callable(callback):
                    callbacks.append(callback)
            elif not pressed and active:
                binding["active"] = False
    for callback in callbacks:
        threading.Thread(target=callback, daemon=True).start()


def _on_press(key) -> None:
    name = _key_to_name(key)
    if not name:
        return
    with _lock:
        _pressed.add(name)
    _dispatch_bindings()


def _on_release(key) -> None:
    name = _key_to_name(key)
    if not name:
        return
    with _lock:
        _pressed.discard(name)
    _dispatch_bindings()


def _ensure_macos_listener() -> bool:
    global _listener
    if _pynput_keyboard is None:
        return False
    with _lock:
        if _listener is not None:
            return True
        try:
            _listener = _pynput_keyboard.Listener(on_press=_on_press, on_release=_on_release)
            _listener.start()
            return True
        except Exception:
            _listener = None
            return False


def add_hotkey(hotkey: str | None, callback: Callable[[], None], suppress: bool = False):
    normalized = normalize_hotkey(hotkey)
    if not normalized:
        return None
    if IS_MAC:
        if not _ensure_macos_listener():
            raise RuntimeError("pynput is required for macOS global hotkeys.")
        binding = {"parts": _parts(normalized), "callback": callback, "active": False}
        with _lock:
            _bindings.append(binding)
        return binding
    if _keyboard is None:
        raise RuntimeError("keyboard package is unavailable.")
    return _keyboard.add_hotkey(normalized, callback, suppress=suppress)


def remove_hotkey(handle) -> None:
    if not handle:
        return
    if IS_MAC:
        with _lock:
            try:
                _bindings.remove(handle)
            except ValueError:
                pass
        return
    if _keyboard is not None:
        _keyboard.remove_hotkey(handle)


def is_pressed(hotkey: str | None) -> bool:
    normalized = normalize_hotkey(hotkey) or ""
    parts = _parts(hotkey)
    if not parts:
        return False
    if IS_MAC:
        _ensure_macos_listener()
        with _lock:
            return parts.issubset(_pressed)
    if _keyboard is None:
        return False
    try:
        return _keyboard.is_pressed(normalized)
    except Exception:
        try:
            return all(_keyboard.is_pressed(part) for part in parts)
        except Exception:
            return False


def send(shortcut: str) -> bool:
    normalized = normalize_hotkey(shortcut) or ""
    if IS_MAC:
        from core.native import send_shortcut

        return send_shortcut(normalized)
    if _keyboard is None:
        return False
    try:
        _keyboard.send(normalized)
        return True
    except Exception:
        return False


def write(text: str, delay: float = 0.01) -> bool:
    if IS_MAC:
        from core.native import type_text

        return type_text(text)
    if _keyboard is None:
        return False
    try:
        _keyboard.write(text, delay=delay)
        return True
    except Exception:
        return False
