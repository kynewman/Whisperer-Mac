"""Output delivery: paste, type, clipboard restore, auto-send."""

from __future__ import annotations

import sys
import time
import threading

try:
    import pyperclip
    _CLIPBOARD_AVAILABLE = True
except Exception:
    _CLIPBOARD_AVAILABLE = False

try:
    from core import hotkeys
    _HOTKEYS_AVAILABLE = True
except Exception:
    hotkeys = None
    _HOTKEYS_AVAILABLE = False


def _paste_shortcut() -> str:
    return "cmd+v" if sys.platform == "darwin" else "ctrl+v"


def _restore_clipboard_later(old: str, delay: float = 0.8):
    def _restore():
        try:
            pyperclip.copy(old)
        except Exception:
            pass
    threading.Timer(delay, _restore).start()


def _native_clipboard_text() -> str:
    if sys.platform != "darwin":
        return ""
    try:
        from core.native import get_clipboard_text

        return get_clipboard_text()
    except Exception:
        return ""


def _set_native_clipboard_text(text: str) -> bool:
    if sys.platform != "darwin":
        return False
    try:
        from core.native import set_clipboard_text

        return set_clipboard_text(text)
    except Exception:
        return False


def _copy_to_clipboard(text: str) -> bool:
    if _set_native_clipboard_text(text):
        deadline = time.time() + 0.12
        while time.time() < deadline:
            current = _native_clipboard_text()
            if not current or current == text:
                return True
            time.sleep(0.01)
        return True
    if not _CLIPBOARD_AVAILABLE:
        return False
    try:
        pyperclip.copy(text)
        deadline = time.time() + 0.35
        while time.time() < deadline:
            try:
                if pyperclip.paste() == text:
                    return True
            except Exception:
                return True
            time.sleep(0.015)
        return True
    except Exception:
        return False


def _wait_for_modifier_release(timeout_s: float = 0.8) -> None:
    if sys.platform != "darwin" or not _HOTKEYS_AVAILABLE or hotkeys is None:
        return
    deadline = time.time() + max(0.0, timeout_s)
    while time.time() < deadline:
        try:
            if not hotkeys.pressed_modifiers():
                return
        except Exception:
            return
        time.sleep(0.015)


def paste_text(
    text: str,
    method: str = "clipboard_paste",
    restore_clipboard: bool = False,
    auto_send: bool = False,
    active_app: str = "",
    paste_delay_ms: int = 50,
    fast_path: bool = False,
) -> bool:
    """
    Deliver text to the active window.

    method:
        clipboard_paste — copy to clipboard then paste shortcut
        simulate_keys   — type character by character
        copy_only       — copy to clipboard but do not paste

    Returns True if the method executed without known errors.
    """
    if method == "copy_only":
        if not _copy_to_clipboard(text):
            raise RuntimeError("Clipboard is unavailable.")
        return True

    old_clipboard = ""
    if restore_clipboard and _CLIPBOARD_AVAILABLE:
        try:
            old_clipboard = pyperclip.paste()
        except Exception:
            old_clipboard = ""

    if method == "clipboard_paste":
        if not _copy_to_clipboard(text):
            raise RuntimeError("Clipboard is unavailable.")
        paste_delay = max(0, int(paste_delay_ms))
        delivered = False
        if sys.platform == "darwin":
            try:
                from core.native import (
                    accessibility_access_granted,
                    insert_text_into_focused_control,
                    paste_clipboard_to_application,
                )

                _wait_for_modifier_release()
                accessibility_ok = accessibility_access_granted()
                if not accessibility_ok:
                    print("PASTE_WARNING accessibility_not_trusted", flush=True)
                min_settle_delay = 20 if fast_path else 75
                delivered = paste_clipboard_to_application(
                    active_app,
                    settle_delay_ms=max(min_settle_delay, paste_delay),
                    expected_text=text,
                    verify=not fast_path,
                )
                if not delivered:
                    print("PASTE_FALLBACK accessibility_insert_after_shortcut", flush=True)
                    delivered = insert_text_into_focused_control(text, active_app)
                if not delivered and not accessibility_ok:
                    raise RuntimeError("macOS Accessibility permission is required for auto-paste.")
            except RuntimeError:
                raise
            except Exception:
                delivered = False
        else:
            # tiny pause so the OS registers the clipboard update
            time.sleep(paste_delay / 1000.0)
            if _HOTKEYS_AVAILABLE and hotkeys is not None:
                delivered = bool(hotkeys.send(_paste_shortcut()))
        if not delivered:
            raise RuntimeError("Paste shortcut was not delivered.")
        if restore_clipboard and _CLIPBOARD_AVAILABLE:
            restore_delay = max(1.2, min(3.0, 0.8 + len(text) / 3000.0))
            _restore_clipboard_later(old_clipboard, delay=restore_delay)
        if auto_send and _HOTKEYS_AVAILABLE and hotkeys is not None:
            if not hotkeys.send("enter"):
                raise RuntimeError("Auto-send shortcut was not delivered.")
        return True

    if method == "simulate_keys":
        if _HOTKEYS_AVAILABLE and hotkeys is not None:
            if not hotkeys.write(text, delay=0.01):
                raise RuntimeError("Simulated typing was not delivered.")
        else:
            raise RuntimeError("Keyboard output is unavailable.")
        if auto_send and _HOTKEYS_AVAILABLE and hotkeys is not None:
            if not hotkeys.send("enter"):
                raise RuntimeError("Auto-send shortcut was not delivered.")
        return True

    return False
