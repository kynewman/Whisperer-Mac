"""System audio ducking during recording."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


class AudioDucker:
    def __init__(self, enabled: bool, duck_percent: int, behavior: str = "lower"):
        self.enabled = enabled
        self.duck_percent = self._snap_percent(duck_percent)
        self.behavior = behavior
        self._original_volume: float | None = None
        self._active = False

    @staticmethod
    def _snap_percent(value: int) -> int:
        return max(0, min(100, int(round(int(value) / 25)) * 25))

    @classmethod
    def from_settings(cls, settings: dict) -> "AudioDucker":
        audio = settings.get("audio", {})
        enabled = bool(audio.get("ducking_enabled", False))
        return cls(enabled, int(audio.get("ducking_percent", 75)), "duck")

    def duck(self) -> bool:
        if not self.enabled or self.duck_percent <= 0 or self._active:
            return False
        if sys.platform == "darwin":
            return self._duck_macos()
        if os.name == "nt":
            return self._duck_windows_noop()
        return False

    def restore(self):
        if sys.platform == "darwin":
            self._restore_macos()
            return
        self._original_volume = None
        self._active = False

    def _duck_windows_noop(self) -> bool:
        # The Mac port keeps the Windows-specific WASAPI code out of import
        # paths. Windows builds can swap this module for their native backend.
        return False

    def _osascript(self, script: str) -> str:
        executable = shutil.which("osascript") or "/usr/bin/osascript"
        try:
            result = subprocess.run(
                [executable, "-e", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=3,
            )
        except Exception:
            return ""
        if result.returncode != 0:
            return ""
        return (result.stdout or "").strip()

    def _duck_macos(self) -> bool:
        try:
            raw = self._osascript("output volume of (get volume settings)")
            original = float(raw)
            ducked = max(0.0, min(100.0, original * (1.0 - self.duck_percent / 100.0)))
            self._osascript(f"set volume output volume {int(round(ducked))}")
            self._original_volume = original
            self._active = True
            print(
                f"System audio ducking applied: {self.duck_percent}% "
                f"({original:.0f} -> {ducked:.0f})",
                flush=True,
            )
            return True
        except Exception as exc:
            print(f"System audio ducking failed: {exc}", flush=True)
            self._original_volume = None
            self._active = False
            return False

    def _restore_macos(self):
        if self._original_volume is None:
            self._active = False
            return
        original = max(0.0, min(100.0, float(self._original_volume)))
        try:
            self._osascript(f"set volume output volume {int(round(original))}")
            print(f"System audio restored: {original:.0f}", flush=True)
        except Exception as exc:
            print(f"System audio restore failed: {exc}", flush=True)
        finally:
            self._original_volume = None
            self._active = False
