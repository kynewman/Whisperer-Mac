"""Lightweight timing helpers for startup and dictation diagnostics."""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager

_TIMING_LOCK = threading.Lock()


def now_ms() -> float:
    return time.perf_counter() * 1000.0


def _timings_path() -> str:
    from core.paths import get_app_data_dir

    return os.path.join(get_app_data_dir(), "performance_timings.jsonl")


def record_timing(label: str, elapsed_ms: float) -> None:
    """Print and persist a timing sample without letting logging affect the hot path."""
    line = f"TIMING {label}={elapsed_ms:.1f}ms"
    print(line, flush=True)
    entry = {
        "ts": time.time(),
        "pid": os.getpid(),
        "label": label,
        "elapsed_ms": round(float(elapsed_ms), 1),
    }
    try:
        with _TIMING_LOCK:
            with open(_timings_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, separators=(",", ":")))
                f.write("\n")
    except Exception:
        pass


def recent_timings(limit: int = 160) -> list[dict]:
    """Return the most recent persisted timing entries."""
    try:
        path = _timings_path()
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        entries = []
        for line in lines:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries
    except Exception:
        return []


def timing_summary(limit: int = 160) -> dict[str, list[dict]]:
    """Group recent timing entries for Diagnostics."""
    startup_prefixes = (
        "engine_",
        "model_",
        "import_",
    )
    dictation_prefixes = (
        "hotkey_",
        "recorder_",
        "context_",
        "dictionary_",
        "transcribe",
        "trim_",
        "format_",
        "paste_",
        "dictation_",
    )
    summary = {"startup": [], "dictation": [], "other": []}
    for entry in recent_timings(limit):
        label = str(entry.get("label", ""))
        if label.startswith(startup_prefixes) or label in {"model_load", "model_warmup"}:
            summary["startup"].append(entry)
        elif label.startswith(dictation_prefixes):
            summary["dictation"].append(entry)
        else:
            summary["other"].append(entry)
    return summary


@contextmanager
def timed(label: str):
    start = now_ms()
    try:
        yield
    finally:
        elapsed = now_ms() - start
        record_timing(label, elapsed)
