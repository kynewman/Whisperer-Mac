"""Launch-on-login helpers."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys


LAUNCH_AGENT_ID = "com.whisperer.app"


def _project_root() -> str:
    return os.environ.get("WHISPERER_PROJECT_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get_exe_path() -> str:
    launcher_exe = os.environ.get("WHISPERER_LAUNCHER_EXE")
    if launcher_exe and os.path.exists(launcher_exe):
        return launcher_exe
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.join(_project_root(), "launcher.py")


def _mac_plist_path() -> str:
    launch_agents = os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents")
    os.makedirs(launch_agents, exist_ok=True)
    return os.path.join(launch_agents, f"{LAUNCH_AGENT_ID}.plist")


def _mac_program_arguments() -> list[str]:
    exe = _get_exe_path()
    if exe.endswith(".py"):
        return [sys.executable, exe]
    return [exe]


def _mac_launchctl(*args: str) -> None:
    try:
        subprocess.run(["launchctl", *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
    except Exception:
        pass


def _mac_is_enabled() -> bool:
    return os.path.exists(_mac_plist_path())


def _mac_set_enabled(enabled: bool) -> bool:
    path = _mac_plist_path()
    if enabled:
        payload = {
            "Label": LAUNCH_AGENT_ID,
            "ProgramArguments": _mac_program_arguments(),
            "RunAtLoad": True,
            "WorkingDirectory": _project_root(),
            "StandardOutPath": os.path.join(os.path.expanduser("~"), "Library", "Logs", "Whisperer.log"),
            "StandardErrorPath": os.path.join(os.path.expanduser("~"), "Library", "Logs", "Whisperer.err.log"),
        }
        try:
            with open(path, "wb") as handle:
                plistlib.dump(payload, handle)
            uid = str(os.getuid())
            _mac_launchctl("bootstrap", f"gui/{uid}", path)
            _mac_launchctl("enable", f"gui/{uid}/{LAUNCH_AGENT_ID}")
            return True
        except Exception as exc:
            print(f"Failed to set launch on login: {exc}", file=sys.stderr)
            return False
    try:
        uid = str(os.getuid())
        _mac_launchctl("bootout", f"gui/{uid}", path)
        if os.path.exists(path):
            os.remove(path)
        return True
    except Exception as exc:
        print(f"Failed to disable launch on login: {exc}", file=sys.stderr)
        return False


if os.name == "nt":
    import winreg

    def is_launch_on_login_enabled() -> bool:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ,
            ) as key:
                value, _ = winreg.QueryValueEx(key, "Whisperer")
                return bool(value)
        except FileNotFoundError:
            return False
        except Exception:
            return False

    def set_launch_on_login(enabled: bool) -> bool:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "Whisperer"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                if enabled:
                    exe = _get_exe_path()
                    if exe.endswith(".py"):
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

else:
    def is_launch_on_login_enabled() -> bool:
        if sys.platform == "darwin":
            return _mac_is_enabled()
        return False

    def set_launch_on_login(enabled: bool) -> bool:
        if sys.platform == "darwin":
            return _mac_set_enabled(enabled)
        return False


def toggle_launch_on_login() -> bool:
    new_state = not is_launch_on_login_enabled()
    if set_launch_on_login(new_state):
        return new_state
    return not new_state
