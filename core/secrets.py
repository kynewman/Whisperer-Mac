"""API key storage wrapper using Windows Credential Manager via keyring."""

from __future__ import annotations

try:
    import keyring
    _KEYRING_AVAILABLE = True
except Exception:
    _KEYRING_AVAILABLE = False


def get_key(service: str) -> str | None:
    """Return the stored API key for a service, or None."""
    if not _KEYRING_AVAILABLE:
        return None
    try:
        value = keyring.get_password("whisperer", service)
        if value == "":
            return None
        return value
    except Exception:
        return None


def set_key(service: str, value: str):
    """Store an API key for a service."""
    if _KEYRING_AVAILABLE:
        try:
            keyring.set_password("whisperer", service, value)
        except Exception:
            pass


def delete_key(service: str):
    """Remove a stored API key."""
    if _KEYRING_AVAILABLE:
        try:
            keyring.delete_password("whisperer", service)
        except Exception:
            pass
