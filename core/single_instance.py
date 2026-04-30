"""Windows single-instance guards for the UI and engine processes."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

ERROR_ALREADY_EXISTS = 183

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.CreateMutexW.restype = wintypes.HANDLE

_HANDLES: list[int] = []


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
