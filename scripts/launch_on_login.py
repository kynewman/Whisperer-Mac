"""Launch on login via Windows registry."""

from __future__ import annotations

import os
import sys
import winreg


def _get_exe_path() -> str:
    """Return the path to the current executable or main script."""
    launcher_exe = os.environ.get("WHISPERER_LAUNCHER_EXE")
    if launcher_exe and os.path.exists(launcher_exe):
        return launcher_exe
    if getattr(sys, "frozen", False):
        return sys.executable
    base = os.environ.get("WHISPERER_PROJECT_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(os.path.dirname(base), "Whisperer.exe") if os.path.basename(base).lower() == "_internal" else "",
        os.path.join(base, "Whisperer.exe"),
        os.path.join(base, "launcher.py"),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return os.path.join(base, "launcher.py")


def is_launch_on_login_enabled() -> bool:
    """Check if Whisperer is set to launch on Windows login."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, "Whisperer")
            return bool(value)
    except FileNotFoundError:
        return False
    except Exception:
        return False


def set_launch_on_login(enabled: bool) -> bool:
    """
    Enable or disable launch on login.
    Returns True on success.
    """
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    app_name = "Whisperer"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                exe = _get_exe_path()
                if exe.endswith(".py"):
                    # In development, use pythonw to avoid console window
                    exe = f'pythonw "{exe}"'
                else:
                    exe = f'"{exe}"'
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, exe)
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
        return True
    except Exception as exc:
        print(f"Failed to set launch on login: {exc}", file=sys.stderr)
        return False


def toggle_launch_on_login() -> bool:
    """Toggle the current state and return the new state."""
    new_state = not is_launch_on_login_enabled()
    if set_launch_on_login(new_state):
        return new_state
    return not new_state
