"""
Context extraction: OCR, selected text, clipboard, and focused-control text.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import shutil
import sys
import threading
import time

import mss
import numpy as np
from PIL import Image

import config
from core import hotkeys
from core import native
from core.term_filter import extract_useful_terms

try:
    import pytesseract

    tesseract_cmd = config.TESSERACT_CMD
    if not os.path.exists(tesseract_cmd):
        tesseract_cmd = shutil.which("tesseract") or tesseract_cmd
    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    _TESSERACT_AVAILABLE = True
except Exception:
    _TESSERACT_AVAILABLE = False

_OCR_CACHE_TTL_S = 12.0
_ocr_cache_lock = threading.Lock()
_ocr_cache: dict[str, object] = {
    "key": None,
    "text": "",
    "ts": 0.0,
    "refreshing": False,
}


def _get_active_window_rect() -> tuple[int, int, int, int] | None:
    """Return (left, top, right, bottom) of the foreground window, or None."""
    if sys.platform == "darwin":
        return native.active_window_rect()
    if os.name != "nt":
        return None
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    rect = ctypes.wintypes.RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return (rect.left, rect.top, rect.right, rect.bottom)
    return None


def _get_active_process_name() -> str:
    """Return the lowercase exe name of the foreground window's process."""
    if sys.platform == "darwin":
        return native.active_window_name()
    if os.name != "nt":
        return ""
    import win32gui
    import win32process
    import psutil

    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return ""
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    try:
        return psutil.Process(pid).name().lower()
    except Exception:
        return ""


def get_active_window_name() -> str:
    """Return the lowercase process name of the currently active window."""
    try:
        return _get_active_process_name()
    except Exception:
        return ""


def get_active_window_title() -> str:
    """Return the title of the currently active foreground window."""
    if sys.platform == "darwin":
        return native.active_window_title()
    try:
        import win32gui

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return ""
        return win32gui.GetWindowText(hwnd) or ""
    except Exception:
        return ""


def _capture_screen_context_uncached(rect: tuple[int, int, int, int]) -> str:
    if not config.OCR_ENABLED or not _TESSERACT_AVAILABLE:
        return ""
    if sys.platform == "darwin" and not native.screen_capture_access_granted():
        return ""

    left, top, right, bottom = rect
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return ""

    try:
        with mss.mss() as sct:
            monitor = {"left": left, "top": top, "width": width, "height": height}
            screenshot = sct.grab(monitor)
            img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
    except Exception:
        return ""

    max_dim = 1920
    if img.width > max_dim or img.height > max_dim:
        ratio = max_dim / max(img.width, img.height)
        img = img.resize(
            (int(img.width * ratio), int(img.height * ratio)),
            Image.Resampling.BILINEAR,
        )

    try:
        raw_text = pytesseract.image_to_string(img, config="--psm 6")
    except Exception:
        return ""

    return " ".join(extract_useful_terms(raw_text, limit=90, source="ocr", include_phrases=False))


def capture_screen_context() -> str:
    """
    Take a screenshot of the active window, run OCR, and return the
    extracted text as a single string of unique words.
    """
    if sys.platform == "darwin" and not native.screen_capture_access_granted():
        return ""
    rect = _get_active_window_rect()
    if rect is None:
        return ""
    text = _capture_screen_context_uncached(rect)
    key = (_get_active_process_name(), rect)
    with _ocr_cache_lock:
        _ocr_cache.update({"key": key, "text": text, "ts": time.time(), "refreshing": False})
    return text


def capture_screen_context_cached(blocking: bool = False) -> str:
    """Return cached OCR immediately and refresh in the background when stale."""
    if sys.platform == "darwin" and not native.screen_capture_access_granted():
        return ""
    rect = _get_active_window_rect()
    if rect is None:
        return ""
    key = (_get_active_process_name(), rect)
    now = time.time()

    with _ocr_cache_lock:
        cached_key = _ocr_cache.get("key")
        cached_text = str(_ocr_cache.get("text") or "")
        cached_ts = float(_ocr_cache.get("ts") or 0.0)
        refreshing = bool(_ocr_cache.get("refreshing"))
        if cached_key == key and now - cached_ts <= _OCR_CACHE_TTL_S:
            return cached_text
        if refreshing and cached_key == key:
            return cached_text
        if blocking:
            _ocr_cache["refreshing"] = True
        else:
            _ocr_cache.update({"key": key, "refreshing": True})

    def _refresh():
        text = _capture_screen_context_uncached(rect)
        with _ocr_cache_lock:
            _ocr_cache.update({"key": key, "text": text, "ts": time.time(), "refreshing": False})

    if blocking:
        _refresh()
        with _ocr_cache_lock:
            return str(_ocr_cache.get("text") or "")

    threading.Thread(target=_refresh, daemon=True).start()
    return cached_text if cached_key == key else ""


# ---------------------------------------------------------------------------
# Selected text via clipboard
# ---------------------------------------------------------------------------

_CLIPBOARD_RESTORE_TIMEOUT = None


def capture_selected_text() -> str:
    """
    Copy via Ctrl+C, read clipboard, restore previous clipboard.
    Returns captured text or empty string if nothing changed.
    """
    try:
        import pyperclip
    except Exception:
        return ""

    before = ""
    try:
        before = pyperclip.paste()
    except Exception:
        pass

    try:
        hotkeys.send("cmd+c" if sys.platform == "darwin" else "ctrl+c")
    except Exception:
        return ""

    time.sleep(0.05)

    after = ""
    try:
        after = pyperclip.paste()
    except Exception:
        pass

    if after == before:
        return ""

    # Restore clipboard in background so it does not block the hot path
    def _restore():
        try:
            pyperclip.copy(before)
        except Exception:
            pass

    threading.Timer(0.2, _restore).start()
    return after


# ---------------------------------------------------------------------------
# Recent clipboard context
# ---------------------------------------------------------------------------

_last_clipboard_change: float = 0.0
_last_clipboard_text: str = ""


def capture_clipboard_context() -> str:
    """Return current clipboard text if it changed within the last 30 seconds."""
    global _last_clipboard_change, _last_clipboard_text
    try:
        import pyperclip
        text = pyperclip.paste()
        if text != _last_clipboard_text:
            _last_clipboard_text = text
            _last_clipboard_change = time.time()
        if time.time() - _last_clipboard_change <= 30:
            return _last_clipboard_text
    except Exception:
        pass
    return ""


def mark_clipboard_pasted():
    """Call after user pastes so we know clipboard context is user-owned."""
    global _last_clipboard_change, _last_clipboard_text
    _last_clipboard_change = 0.0
    _last_clipboard_text = ""


# ---------------------------------------------------------------------------
# Focused-control text
# ---------------------------------------------------------------------------

def capture_ui_automation_text(hwnd: int | None = None) -> str:
    """Pull focused-control text where the platform exposes it. Falls back to ''."""
    if sys.platform == "darwin":
        return native.focused_control_text()
    if os.name != "nt":
        return ""
    try:
        import comtypes.client
        UIAutomation = comtypes.client.CreateObject("UIAutomationClient.CUIAutomation")
        if hwnd is None:
            import win32gui
            hwnd = win32gui.GetForegroundWindow()
        element = UIAutomation.GetFocusedElement()
        if element is None:
            return ""
        text = element.CurrentName or ""
        value_pattern = element.GetCurrentPattern(10002)  # ValuePattern
        if value_pattern:
            text += " " + (value_pattern.CurrentValue or "")
        return text.strip()
    except Exception:
        return ""
