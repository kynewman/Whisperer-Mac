"""API key storage wrapper using the platform keychain with a local fallback."""

from __future__ import annotations

import json
import os

try:
    import keyring
    _KEYRING_AVAILABLE = True
except Exception:
    _KEYRING_AVAILABLE = False


def _fallback_path() -> str:
    try:
        from core.paths import get_app_data_dir

        base_dir = get_app_data_dir()
    except Exception:
        base_dir = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Whisperer")
        os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, "api_keys.json")


def _read_fallback() -> dict[str, str]:
    try:
        with open(_fallback_path(), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items() if isinstance(value, str)}


def _write_fallback(keys: dict[str, str]) -> None:
    path = _fallback_path()
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(keys, fh, separators=(",", ":"))
            fh.write("\n")
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def get_key(service: str) -> str | None:
    """Return the stored API key for a service, or None."""
    service = (service or "").strip()
    fallback = _read_fallback().get(service)
    if fallback:
        return fallback
    try:
        if not _KEYRING_AVAILABLE:
            return None
        value = keyring.get_password("whisperer", service)
        if value:
            return value
        if value == "":
            return None
        return value
    except Exception:
        return None


def set_key(service: str, value: str):
    """Store an API key for a service."""
    service = (service or "").strip()
    value = (value or "").strip()
    if not service:
        return
    keys = _read_fallback()
    if value:
        keys[service] = value
    else:
        keys.pop(service, None)
    _write_fallback(keys)
    if _KEYRING_AVAILABLE:
        try:
            keyring.set_password("whisperer", service, value)
        except Exception:
            pass


def delete_key(service: str):
    """Remove a stored API key."""
    service = (service or "").strip()
    keys = _read_fallback()
    if service in keys:
        keys.pop(service, None)
        _write_fallback(keys)
    if _KEYRING_AVAILABLE:
        try:
            keyring.delete_password("whisperer", service)
        except Exception:
            pass
