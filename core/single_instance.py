"""Single-instance guards for the UI and engine processes."""

from __future__ import annotations

import os
import tempfile

_HANDLES: list[object] = []


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    ERROR_ALREADY_EXISTS = 183

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    _kernel32.CreateMutexW.restype = wintypes.HANDLE

    def acquire(name: str) -> bool:
        """Acquire a named process mutex and keep it alive for this process."""
        handle = _kernel32.CreateMutexW(None, False, f"Local\\{name}")
        if not handle:
            return True
        already_exists = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        if already_exists:
            return False
        _HANDLES.append(handle)
        return True

else:
    import fcntl

    def acquire(name: str) -> bool:
        """Acquire a non-blocking user-local lock file."""
        lock_dir = os.path.join(tempfile.gettempdir(), "whisperer-locks")
        os.makedirs(lock_dir, exist_ok=True)
        safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in name)
        lock_path = os.path.join(lock_dir, f"{safe_name}.lock")
        handle = open(lock_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        _HANDLES.append(handle)
        return True
