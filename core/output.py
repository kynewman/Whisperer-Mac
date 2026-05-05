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


def _copy_to_clipboard(text: str) -> bool:
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


def _wait_for_modifier_release(timeout_s: float = 0.45) -> None:
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
                if not accessibility_access_granted():
                    print("PASTE_WARNING accessibility_not_trusted", flush=True)
                delivered = paste_clipboard_to_application(
                    active_app,
                    settle_delay_ms=max(75, paste_delay),
                )
                if not delivered:
                    delivered = insert_text_into_focused_control(text, active_app)
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
            _restore_clipboard_later(old_clipboard)
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
