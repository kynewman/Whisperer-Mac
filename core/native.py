"""Small cross-platform native helpers for app integration."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import ctypes
import ctypes.util


IS_MAC = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"

_MAC_KEY_CODES = {
    "a": 0,
    "s": 1,
    "d": 2,
    "f": 3,
    "h": 4,
    "g": 5,
    "z": 6,
    "x": 7,
    "c": 8,
    "v": 9,
    "b": 11,
    "q": 12,
    "w": 13,
    "e": 14,
    "r": 15,
    "y": 16,
    "t": 17,
    "1": 18,
    "2": 19,
    "3": 20,
    "4": 21,
    "6": 22,
    "5": 23,
    "equals": 24,
    "9": 25,
    "7": 26,
    "minus": 27,
    "8": 28,
    "0": 29,
    "right bracket": 30,
    "o": 31,
    "u": 32,
    "left bracket": 33,
    "i": 34,
    "p": 35,
    "enter": 36,
    "return": 36,
    "l": 37,
    "j": 38,
    "quote": 39,
    "k": 40,
    "semicolon": 41,
    "backslash": 42,
    "comma": 43,
    "slash": 44,
    "n": 45,
    "m": 46,
    "period": 47,
    "tab": 48,
    "space": 49,
    "grave": 50,
    "delete": 51,
    "backspace": 51,
    "escape": 53,
    "esc": 53,
    "left": 123,
    "right": 124,
    "down": 125,
    "up": 126,
}


def app_support_dir(app_name: str = "Whisperer") -> str:
    """Return a per-user application support directory."""
    if IS_MAC:
        root = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    elif IS_WINDOWS:
        root = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        root = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    path = os.path.join(root, app_name)
    os.makedirs(path, exist_ok=True)
    return path


def screen_capture_access_granted() -> bool:
    """Return whether macOS Screen Recording access is already granted.

    This deliberately uses the non-prompting preflight API. Callers should not
    trigger the system permission dialog during a dictation hot path.
    """
    if not IS_MAC:
        return True
    try:
        services_path = (
            ctypes.util.find_library("ApplicationServices")
            or "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        services = ctypes.CDLL(services_path)
        preflight = services.CGPreflightScreenCaptureAccess
        preflight.argtypes = []
        preflight.restype = ctypes.c_bool
        return bool(preflight())
    except Exception:
        return False


def _run_osascript(script: str, timeout: float = 1.0) -> str:
    if not IS_MAC:
        return ""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def _applescript_string(value: str) -> str:
    parts = str(value).split("\n")
    quoted = ['"' + part.replace("\\", "\\\\").replace('"', '\\"') + '"' for part in parts]
    return " & linefeed & ".join(quoted) if quoted else '""'


def accessibility_access_granted() -> bool:
    """Return whether this process is trusted for Accessibility event posting."""
    if not IS_MAC:
        return True
    try:
        services_path = (
            ctypes.util.find_library("ApplicationServices")
            or "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        services = ctypes.CDLL(services_path)
        trusted = services.AXIsProcessTrusted
        trusted.argtypes = []
        trusted.restype = ctypes.c_bool
        return bool(trusted())
    except Exception:
        return False


def request_accessibility_access(prompt: bool = False) -> bool:
    """Return Accessibility trust and optionally ask macOS to show the permission prompt."""
    if not IS_MAC:
        return True
    try:
        import Quartz

        options = None
        if prompt:
            key = getattr(Quartz, "kAXTrustedCheckOptionPrompt", "AXTrustedCheckOptionPrompt")
            options = {key: True}
        trusted_with_options = getattr(Quartz, "AXIsProcessTrustedWithOptions", None)
        if callable(trusted_with_options):
            return bool(trusted_with_options(options or {}))
    except Exception:
        pass
    return accessibility_access_granted()


def set_clipboard_text(text: str) -> bool:
    """Set macOS pasteboard text without spawning pbcopy."""
    if not IS_MAC:
        return False
    try:
        import AppKit

        pasteboard = AppKit.NSPasteboard.generalPasteboard()
        pasteboard_type = getattr(AppKit, "NSPasteboardTypeString", "public.utf8-plain-text")
        pasteboard.clearContents()
        return bool(pasteboard.setString_forType_(str(text), pasteboard_type))
    except Exception:
        return False


def get_clipboard_text() -> str:
    """Return macOS pasteboard text without spawning pbpaste."""
    if not IS_MAC:
        return ""
    try:
        import AppKit

        pasteboard = AppKit.NSPasteboard.generalPasteboard()
        pasteboard_type = getattr(AppKit, "NSPasteboardTypeString", "public.utf8-plain-text")
        return str(pasteboard.stringForType_(pasteboard_type) or "")
    except Exception:
        return ""


def _appkit_frontmost_application_name() -> str:
    if not IS_MAC:
        return ""
    try:
        from AppKit import NSWorkspace

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return ""
        return str(app.localizedName() or app.bundleIdentifier() or "").strip()
    except Exception:
        return ""


def _activate_application_appkit(process_name: str) -> bool:
    if not IS_MAC:
        return False
    target = (process_name or "").strip().lower()
    if not target:
        return False
    try:
        import AppKit

        workspace = AppKit.NSWorkspace.sharedWorkspace()
        options = getattr(AppKit, "NSApplicationActivateIgnoringOtherApps", 2)
        for app in workspace.runningApplications():
            localized = str(app.localizedName() or "").strip()
            bundle_id = str(app.bundleIdentifier() or "").strip()
            candidates = {localized.lower(), bundle_id.lower()}
            if target not in candidates:
                continue
            if bool(app.activateWithOptions_(options)):
                time.sleep(0.06)
                return True
    except Exception:
        return False
    return False


def active_window_name() -> str:
    if IS_MAC:
        appkit_name = _appkit_frontmost_application_name()
        if appkit_name:
            return appkit_name
        return _run_osascript(
            """
            tell application "System Events"
              set frontProcess to first application process whose frontmost is true
              return name of frontProcess
            end tell
            """
        )
    return ""


def active_window_title() -> str:
    if IS_MAC:
        return _run_osascript(
            """
            tell application "System Events"
              set frontProcess to first application process whose frontmost is true
              try
                return name of front window of frontProcess
              on error
                return ""
              end try
            end tell
            """
        )
    return ""


def active_window_rect() -> tuple[int, int, int, int] | None:
    if not IS_MAC:
        return None
    raw = _run_osascript(
        """
        tell application "System Events"
          set frontProcess to first application process whose frontmost is true
          try
            set frontWindow to front window of frontProcess
            set {x, y} to position of frontWindow
            set {w, h} to size of frontWindow
            return (x as integer) & "," & (y as integer) & "," & ((x + w) as integer) & "," & ((y + h) as integer)
          on error
            return ""
          end try
        end tell
        """
    )
    try:
        left, top, right, bottom = [int(part.strip()) for part in raw.split(",", 3)]
    except Exception:
        return None
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def focused_control_text(process_name: str = "") -> str:
    if not IS_MAC:
        return ""
    target = (process_name or "").strip()
    if target:
        escaped = target.replace("\\", "\\\\").replace('"', '\\"')
        process_clause = f"""
          ignoring case
            set frontProcess to first application process whose name is "{escaped}"
          end ignoring
        """
    else:
        process_clause = '          set frontProcess to first application process whose frontmost is true'
    return _run_osascript(
        f"""
        tell application "System Events"
{process_clause}
          try
            set focusedElement to value of attribute "AXFocusedUIElement" of frontProcess
            set outText to ""
            try
              set outText to outText & (name of focusedElement as string)
            end try
            try
              set fieldValue to value of focusedElement
              if fieldValue is not missing value then set outText to outText & " " & (fieldValue as string)
            end try
            return outText
          on error
            return ""
          end try
        end tell
        """,
        timeout=1.2,
    ).strip()


def focused_control_preceding_text(process_name: str = "", limit: int = 80) -> str:
    """Return text immediately before the insertion point when Accessibility exposes it."""
    if not IS_MAC:
        return ""
    target = (process_name or "").strip()
    if target:
        escaped = target.replace("\\", "\\\\").replace('"', '\\"')
        process_clause = f"""
          ignoring case
            set frontProcess to first application process whose name is "{escaped}"
          end ignoring
        """
    else:
        process_clause = '          set frontProcess to first application process whose frontmost is true'
    raw = _run_osascript(
        f"""
        tell application "System Events"
{process_clause}
          try
            set focusedElement to value of attribute "AXFocusedUIElement" of frontProcess
            set fieldValue to ""
            try
              set fieldValue to value of focusedElement as string
            end try
            if fieldValue is "" then return ""

            set cursorIndex to -1
            try
              set selectedRange to value of attribute "AXSelectedTextRange" of focusedElement
              try
                set cursorIndex to item 1 of selectedRange
              on error
                set cursorIndex to location of selectedRange
              end try
            end try
            if cursorIndex < 0 then return ""
            if cursorIndex = 0 then return ""

            set textLength to length of fieldValue
            if cursorIndex > textLength then set cursorIndex to textLength
            set startIndex to cursorIndex - {max(1, int(limit))} + 1
            if startIndex < 1 then set startIndex to 1
            return text startIndex thru cursorIndex of fieldValue
          on error
            return ""
          end try
        end tell
        """,
        timeout=0.8,
    )
    return raw[-max(1, int(limit)) :]


def focused_control_snapshot(process_name: str = "", value_limit: int = 2000, timeout: float = 0.8) -> dict[str, str]:
    """Return focused-control text and selection metadata when Accessibility exposes it."""
    if not IS_MAC:
        return {}
    target = (process_name or "").strip()
    if target:
        escaped = target.replace("\\", "\\\\").replace('"', '\\"')
        process_clause = f"""
          ignoring case
            set frontProcess to first application process whose name is "{escaped}"
          end ignoring
        """
    else:
        process_clause = '          set frontProcess to first application process whose frontmost is true'
    raw = _run_osascript(
        f"""
        tell application "System Events"
{process_clause}
          try
            set focusedElement to value of attribute "AXFocusedUIElement" of frontProcess
            set fieldValue to ""
            try
              set maybeValue to value of focusedElement
              if maybeValue is not missing value then set fieldValue to maybeValue as string
            end try
            set fieldValueLength to length of fieldValue
            set selectionText to ""
            try
              set maybeSelection to value of attribute "AXSelectedText" of focusedElement
              if maybeSelection is not missing value then set selectionText to maybeSelection as string
            end try
            set rangeText to ""
            try
              set selectedRange to value of attribute "AXSelectedTextRange" of focusedElement
              try
                set rangeText to (item 1 of selectedRange as string) & ":" & (item 2 of selectedRange as string)
              on error
                set rangeText to (location of selectedRange as string) & ":" & (length of selectedRange as string)
              end try
            end try
            set separatorText to "<<<WHISPERER_AX_SEP>>>"
            return fieldValue & separatorText & selectionText & separatorText & rangeText & separatorText & (fieldValueLength as string)
          on error
            return ""
          end try
        end tell
        """,
        timeout=max(0.05, float(timeout)),
    )
    if not raw:
        return {}
    parts = raw.split("<<<WHISPERER_AX_SEP>>>", 3)
    value = parts[0] if parts else ""
    value_length = len(value)
    if len(parts) > 3:
        try:
            value_length = max(0, int(parts[3]))
        except (TypeError, ValueError):
            value_length = len(value)
    if len(value) > value_limit:
        value = value[-value_limit:]
    return {
        "value": value,
        "selected_text": parts[1] if len(parts) > 1 else "",
        "selected_range": parts[2] if len(parts) > 2 else "",
        "value_length": str(value_length),
    }


def _parse_selected_range(value: str) -> tuple[int, int] | None:
    cleaned = str(value or "").strip().replace(",", ":")
    if not cleaned or ":" not in cleaned:
        return None
    left, right = cleaned.split(":", 1)
    try:
        location = max(0, int(float(left.strip())))
        length = max(0, int(float(right.strip())))
        return location, length
    except (TypeError, ValueError):
        return None


def preceding_text_from_snapshot(snapshot: dict[str, str] | None, limit: int = 80) -> tuple[str, bool, bool]:
    """Derive text before the insertion point from a focused-control snapshot.

    Returns (preceding_text, known, cursor_at_start). A false "known" means the
    control did not expose enough data quickly enough and callers should use
    their fallback spacing behavior.
    """
    if not snapshot:
        return "", False, False
    selected_range = _parse_selected_range(str(snapshot.get("selected_range", "")))
    if selected_range is None:
        return "", False, False
    cursor_index, selection_length = selected_range
    cursor_at_start = cursor_index == 0 and selection_length == 0
    if cursor_at_start:
        return "", True, True
    value = str(snapshot.get("value", "") or "")
    try:
        value_length = max(len(value), int(str(snapshot.get("value_length", len(value)))))
    except (TypeError, ValueError):
        value_length = len(value)
    if not value:
        return "", False, cursor_at_start
    truncated_start = max(0, value_length - len(value))
    if cursor_index < truncated_start:
        return "", False, cursor_at_start
    relative_cursor = max(0, min(len(value), cursor_index - truncated_start))
    if relative_cursor <= 0:
        return "", False, cursor_at_start
    return value[max(0, relative_cursor - max(1, int(limit))) : relative_cursor], True, cursor_at_start


def activate_application_process(process_name: str) -> bool:
    """Bring a macOS application process back to the front without clicking."""
    if not IS_MAC:
        return False
    name = (process_name or "").strip()
    if not name:
        return False
    if _activate_application_appkit(name):
        return True
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    result = _run_osascript(
        f"""
        tell application "System Events"
          try
            ignoring case
              set targetProcess to first application process whose name is "{escaped}"
            end ignoring
            set frontmost of targetProcess to true
            return "ok"
          on error
            return ""
          end try
        end tell
        """,
        timeout=1.5,
    )
    if result == "ok":
        time.sleep(0.06)
        return True
    return False


def _shortcut_parts(shortcut: str) -> tuple[list[str], str]:
    parts = [part.strip().lower() for part in shortcut.split("+") if part.strip()]
    if not parts:
        return [], ""
    lookup = {
        "control": "ctrl",
        "command": "cmd",
        "meta": "cmd",
        "option": "alt",
        "return": "enter",
        "esc": "escape",
        "=": "equals",
        "-": "minus",
        "[": "left bracket",
        "]": "right bracket",
        "'": "quote",
        ";": "semicolon",
        "\\": "backslash",
        ",": "comma",
        "/": "slash",
        "`": "grave",
        ".": "period",
    }
    normalized = [lookup.get(part, part) for part in parts]
    return normalized[:-1], normalized[-1]


def _send_shortcut_quartz(shortcut: str) -> bool:
    """Post a shortcut through Quartz so paste does not depend on System Events automation."""
    if not IS_MAC:
        return False
    modifiers, key = _shortcut_parts(shortcut)
    key_code = _MAC_KEY_CODES.get(key)
    if key_code is None:
        return False
    if not accessibility_access_granted():
        return False
    try:
        import Quartz

        flags = 0
        if any(part in {"cmd", "command", "meta"} for part in modifiers):
            flags |= Quartz.kCGEventFlagMaskCommand
        if any(part in {"ctrl", "control"} for part in modifiers):
            flags |= Quartz.kCGEventFlagMaskControl
        if any(part in {"alt", "option"} for part in modifiers):
            flags |= Quartz.kCGEventFlagMaskAlternate
        if "shift" in modifiers:
            flags |= Quartz.kCGEventFlagMaskShift
        if "fn" in modifiers and hasattr(Quartz, "kCGEventFlagMaskSecondaryFn"):
            flags |= Quartz.kCGEventFlagMaskSecondaryFn

        source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        down = Quartz.CGEventCreateKeyboardEvent(source, key_code, True)
        up = Quartz.CGEventCreateKeyboardEvent(source, key_code, False)
        if down is None or up is None:
            return False
        Quartz.CGEventSetFlags(down, flags)
        Quartz.CGEventSetFlags(up, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        time.sleep(0.018)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
        time.sleep(0.035)
        return True
    except Exception:
        return False


def _snapshot_changed_after_insert(before: dict[str, str], after: dict[str, str], text: str) -> bool | None:
    if not before or not after:
        return None
    before_value = before.get("value", "")
    after_value = after.get("value", "")
    before_range = before.get("selected_range", "")
    after_range = after.get("selected_range", "")
    if not before_value and not after_value and not before_range and not after_range:
        return None
    if after_value != before_value:
        if text and (text.strip() in after_value or len(after_value) >= len(before_value) + min(len(text), 4)):
            return True
        return True
    if after_range != before_range:
        return True
    return False


def paste_clipboard_to_application(
    process_name: str = "",
    settle_delay_ms: int = 80,
    expected_text: str = "",
    verify: bool = False,
    verify_timeout_ms: int = 220,
) -> bool:
    """Activate the target macOS app and deliver the current clipboard with Cmd+V."""
    if not IS_MAC:
        return False
    target = (process_name or "").strip()
    if target:
        current = active_window_name()
        if current.lower() != target.lower() and not activate_application_process(target):
            return False
        deadline = time.time() + 1.2
        while time.time() < deadline:
            if active_window_name().lower() == target.lower():
                break
            time.sleep(0.03)
        if active_window_name().lower() != target.lower():
            return False
    before = focused_control_snapshot(target, timeout=0.22) if verify and expected_text else {}
    time.sleep(max(0, int(settle_delay_ms)) / 1000.0)
    if not send_shortcut("cmd+v"):
        return False
    if not verify or not expected_text:
        return True
    time.sleep(max(0.08, min(0.45, int(verify_timeout_ms) / 1000.0)))
    after = focused_control_snapshot(target, timeout=0.22)
    changed = _snapshot_changed_after_insert(before, after, expected_text)
    if changed is False:
        print("PASTE_VERIFY no_focused_text_change_after_shortcut", flush=True)
        return False
    return True


def insert_text_into_focused_control(text: str, process_name: str = "") -> bool:
    """Insert text directly into the focused macOS control using Accessibility."""
    if not IS_MAC or not text:
        return False
    target = (process_name or "").strip()
    if target:
        current = active_window_name()
        if current.lower() != target.lower() and not activate_application_process(target):
            return False
        time.sleep(0.08)
    text_literal = _applescript_string(text)
    result = _run_osascript(
        f"""
        tell application "System Events"
          try
            set frontProcess to first application process whose frontmost is true
            set focusedElement to value of attribute "AXFocusedUIElement" of frontProcess
            try
              set selected text of focusedElement to {text_literal}
              return "ok"
            end try
            try
              set value of attribute "AXSelectedText" of focusedElement to {text_literal}
              return "ok"
            end try
            try
              keystroke {text_literal}
              return "ok"
            end try
            return ""
          on error
            return ""
          end try
        end tell
        """,
        timeout=max(1.5, min(12.0, len(text) / 18.0)),
    )
    return result == "ok"


def _modifier_clause(parts: list[str]) -> str:
    modifiers = []
    for part in parts:
        if part in {"cmd", "command", "meta", "win", "windows", "left windows", "right windows"}:
            modifiers.append("command down")
        elif part in {"ctrl", "control"}:
            modifiers.append("control down")
        elif part in {"alt", "option", "menu"}:
            modifiers.append("option down")
        elif part == "shift":
            modifiers.append("shift down")
    return " using {" + ", ".join(dict.fromkeys(modifiers)) + "}" if modifiers else ""


def send_shortcut(shortcut: str) -> bool:
    """Send a keyboard shortcut to the active app."""
    if not IS_MAC:
        return False
    if _send_shortcut_quartz(shortcut):
        return True
    modifiers_list, key = _shortcut_parts(shortcut)
    parts = [*modifiers_list, key] if key else []
    if not parts:
        return False
    modifiers = _modifier_clause(parts[:-1])
    keycodes = {key_name: code for key_name, code in _MAC_KEY_CODES.items() if code >= 36 or key_name in {"space"}}
    if key in keycodes:
        script = f"""
        tell application "System Events"
          key code {keycodes[key]}{modifiers}
          return "ok"
        end tell
        """
    else:
        escaped = key.replace("\\", "\\\\").replace('"', '\\"')
        script = f"""
        tell application "System Events"
          keystroke "{escaped}"{modifiers}
          return "ok"
        end tell
        """
    return _run_osascript(script, timeout=1.2) == "ok"


def type_text(text: str) -> bool:
    if not IS_MAC:
        return False
    try:
        from pynput.keyboard import Controller

        Controller().type(text)
        return True
    except Exception:
        pass
    # Fallback for environments where pynput is unavailable. This is slower,
    # but keeps simulate-keys mode functional for plain text.
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    script = f"""
    tell application "System Events"
      keystroke "{escaped}"
      return "ok"
    end tell
    """
    return _run_osascript(script, timeout=max(1.0, min(10.0, len(text) / 20.0))) == "ok"


def copy_selection_to_clipboard() -> None:
    if IS_MAC:
        send_shortcut("cmd+c")
    else:
        return
    time.sleep(0.08)
