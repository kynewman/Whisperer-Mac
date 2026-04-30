"""Release smoke checks for the installed Whisperer UI/engine split."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


def _check(label: str, ok: bool, message: str = "") -> bool:
    status = "OK" if ok else "FAIL"
    print(f"{status} - {label}: {message}")
    return ok


def main() -> int:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    install_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Whisperer")
    exe_path = os.path.join(install_dir, "Whisperer.exe")
    python_exe = next(
        (
            candidate
            for candidate in (
                os.environ.get("WHISPERER_PYTHON"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python", "Python310", "python.exe"),
                shutil.which("python"),
            )
            if candidate and os.path.exists(candidate)
        ),
        None,
    )

    ok = True
    ok &= _check("installed exe", os.path.exists(exe_path), exe_path)
    ok &= _check("external python", bool(python_exe), str(python_exe))
    ok &= _check("engine source", os.path.exists(os.path.join(project_root, "main.py")), project_root)

    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        ok &= _check("nvidia-smi", result.returncode == 0, result.stdout.strip().splitlines()[0] if result.stdout else "")
    except Exception as exc:
        ok &= _check("nvidia-smi", False, str(exc))

    if python_exe and os.path.exists(python_exe):
        result = subprocess.run(
            [python_exe, "-c", "import torch; print(torch.cuda.is_available())"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        ok &= _check("external python cuda", result.returncode == 0 and "True" in result.stdout, result.stdout.strip() or result.stderr.strip())

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
