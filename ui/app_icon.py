"""Shared app icon path resolution for source and frozen builds."""

from __future__ import annotations

import os
import sys

APP_USER_MODEL_ID = "Whisperer.Windows"
APP_ICON_FILENAME = "whisperer.ico"


def app_icon_path() -> str:
    """Return the best available path to the Whisperer app icon."""
    candidates: list[str] = []
    if getattr(sys, "frozen", False):
        candidates.extend(
            [
                os.path.join(getattr(sys, "_MEIPASS", ""), "assets", APP_ICON_FILENAME),
                os.path.join(os.path.dirname(sys.executable), "_internal", "assets", APP_ICON_FILENAME),
                os.path.join(os.path.dirname(sys.executable), "assets", APP_ICON_FILENAME),
            ]
        )
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates.append(os.path.join(project_root, "assets", APP_ICON_FILENAME))
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return candidates[-1]
