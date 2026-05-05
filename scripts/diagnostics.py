"""Health checks for Whisperer dependencies."""

from __future__ import annotations

import shutil
import subprocess
import sys


def _check_acceleration() -> dict:
    if sys.platform == "darwin":
        try:
            import torch
            if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                return {"name": "Acceleration", "ok": True, "message": "Apple Metal (MPS) available"}
            return {"name": "Acceleration", "ok": True, "message": "CPU mode"}
        except Exception as exc:
            return {"name": "Acceleration", "ok": True, "message": f"CPU mode ({exc})"}
    try:
        import torch
        available = torch.cuda.is_available()
        name = torch.cuda.get_device_name(0) if available else "None"
        return {"name": "Acceleration", "ok": available, "message": name}
    except Exception as exc:
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                first = result.stdout.strip().splitlines()[0]
                count = len(result.stdout.strip().splitlines())
                return {"name": "Acceleration", "ok": True, "message": f"{count} GPU(s): {first}"}
        except Exception:
            pass
        return {"name": "Acceleration", "ok": False, "message": str(exc)}


def _check_microphone() -> dict:
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        inputs = [d for d in devices if d.get("max_input_channels", 0) > 0]
        return {"name": "Microphone", "ok": len(inputs) > 0, "message": f"{len(inputs)} input device(s) found"}
    except Exception as exc:
        return {"name": "Microphone", "ok": False, "message": str(exc)}


def _check_ocr() -> dict:
    try:
        import pytesseract
        version = pytesseract.get_tesseract_version()
        return {"name": "OCR (Tesseract)", "ok": True, "message": f"v{version}"}
    except Exception as exc:
        return {"name": "OCR (Tesseract)", "ok": False, "message": str(exc)}


def _check_ffmpeg() -> dict:
    path = shutil.which("ffmpeg")
    if not path:
        return {"name": "ffmpeg", "ok": False, "message": "Not found on PATH"}
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        first_line = result.stdout.splitlines()[0] if result.stdout else ""
        return {"name": "ffmpeg", "ok": True, "message": first_line}
    except Exception as exc:
        return {"name": "ffmpeg", "ok": False, "message": str(exc)}


def _check_keyring() -> dict:
    try:
        import keyring
        keyring.get_keyring()
        return {"name": "Keyring", "ok": True, "message": "Backend available"}
    except Exception as exc:
        return {"name": "Keyring", "ok": False, "message": str(exc)}


def _check_macos_accessibility() -> dict:
    if sys.platform != "darwin":
        return {"name": "macOS Accessibility", "ok": True, "message": "Not required on this platform"}
    try:
        from core import native

        trusted = native.accessibility_access_granted()
        active = native.active_window_name() or "unknown"
        if trusted:
            return {"name": "macOS Accessibility", "ok": True, "message": f"Trusted; front app: {active}"}
        return {
            "name": "macOS Accessibility",
            "ok": False,
            "message": f"Not trusted; paste hotkeys may be blocked. Front app: {active}",
        }
    except Exception as exc:
        return {"name": "macOS Accessibility", "ok": False, "message": str(exc)}


def _check_file_transcription() -> dict:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"name": "File Transcription", "ok": False, "message": "ffmpeg not found"}
    try:
        from core.file_transcriber import SUPPORTED_EXTENSIONS
        result = subprocess.run(
            ["ffmpeg", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "0.1", "-f", "f32le", "pipe:1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        if result.returncode == 0 and len(result.stdout) > 0:
            return {"name": "File Transcription", "ok": True, "message": f"ffmpeg ready ({len(SUPPORTED_EXTENSIONS)} formats)"}
        return {"name": "File Transcription", "ok": False, "message": "ffmpeg test failed"}
    except Exception as exc:
        return {"name": "File Transcription", "ok": False, "message": str(exc)}


def run_diagnostics() -> list[dict]:
    return [
        _check_acceleration(),
        _check_microphone(),
        _check_ocr(),
        _check_ffmpeg(),
        _check_keyring(),
        _check_macos_accessibility(),
        _check_file_transcription(),
    ]


def _format_timing_block(title: str, entries: list[dict], count: int = 12) -> list[str]:
    if not entries:
        return [f"{title}: no timing samples yet"]
    lines = [f"{title}:"]
    for entry in entries[-count:]:
        label = entry.get("label", "unknown")
        elapsed = entry.get("elapsed_ms", "?")
        lines.append(f"  {label}: {elapsed} ms")
    return lines


def diagnostics_text() -> str:
    from core.perf import timing_summary

    results = run_diagnostics()
    lines = [f"{'OK' if r['ok'] else 'FAIL'} - {r['name']}: {r['message']}" for r in results]
    timings = timing_summary()
    lines.append("")
    lines.extend(_format_timing_block("Last startup timings", timings["startup"]))
    lines.append("")
    lines.extend(_format_timing_block("Last dictation timings", timings["dictation"]))
    return "\n".join(lines)


def export_diagnostics_bundle(output_path: str) -> str:
    """
    Export a diagnostics bundle ZIP containing:
    - diagnostics.json (structured health check results)
    - settings.json (redacted: API keys removed)
    - recent log excerpt (if available)
    Returns the path to the created ZIP file.
    """
    import json as _json
    import os
    import zipfile
    from datetime import datetime

    from core.paths import get_app_data_dir
    from core.settings import load_settings

    results = run_diagnostics()
    settings = load_settings()

    # Redact sensitive keys
    safe_settings = dict(settings)
    for section in ("llm", "shortcuts", "paste"):
        if section in safe_settings:
            safe_settings[section] = {k: "***" if "key" in k.lower() or "url" in k.lower() else v for k, v in safe_settings[section].items()}

    bundle_dir = os.path.join(get_app_data_dir(), "diagnostics")
    os.makedirs(bundle_dir, exist_ok=True)

    if not output_path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(bundle_dir, f"whisperer_diagnostics_{ts}.zip")

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("diagnostics.json", _json.dumps(results, indent=2))
        zf.writestr("settings.json", _json.dumps(safe_settings, indent=2))

        # Include a small excerpt of the database size
        db_path = os.path.join(get_app_data_dir(), "whisperer.db")
        if os.path.exists(db_path):
            db_size = os.path.getsize(db_path)
            zf.writestr("db_info.txt", f"Database path: {db_path}\nSize: {db_size} bytes\n")

    return output_path


if __name__ == "__main__":
    for r in run_diagnostics():
        status = "OK" if r["ok"] else "FAIL"
        print(f"{status} — {r['name']}: {r['message']}")
