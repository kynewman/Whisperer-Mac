"""
=============================================================================
  ENGINE TEST — Whisper Project
=============================================================================
  Run this file FIRST to make sure your RTX 5090, CUDA, and Whisper v3 are
  all working. It checks every dependency one by one and tells you exactly
  what to do if something is wrong.

  Usage:
      python engine_test.py
=============================================================================
"""

import os
import sys
import platform
import subprocess
import shutil
import time

# ── Colour helpers (work on Windows thanks to colorama) ──────────────────────
try:
    from colorama import init as colorama_init, Fore, Style
    colorama_init()
except ImportError:
    class _Dummy:
        def __getattr__(self, _):
            return ""
    Fore = Style = _Dummy()

PASS = f"{Fore.GREEN}PASS{Style.RESET_ALL}"
FAIL = f"{Fore.RED}FAIL{Style.RESET_ALL}"
WARN = f"{Fore.YELLOW}WARN{Style.RESET_ALL}"
INFO = f"{Fore.CYAN}INFO{Style.RESET_ALL}"
BOLD = Style.BRIGHT if hasattr(Style, "BRIGHT") else ""
RESET = Style.RESET_ALL if hasattr(Style, "RESET_ALL") else ""

DIVIDER = "=" * 72
THIN = "-" * 72

all_passed = True
fixes: list[str] = []


def header(title: str):
    print(f"\n{DIVIDER}")
    print(f"  {BOLD}{title}{RESET}")
    print(DIVIDER)


def result(label: str, ok: bool, detail: str = ""):
    global all_passed
    tag = PASS if ok else FAIL
    if not ok:
        all_passed = False
    msg = f"  [{tag}]  {label}"
    if detail:
        msg += f"  —  {detail}"
    print(msg)


def warn(label: str, detail: str = ""):
    msg = f"  [{WARN}]  {label}"
    if detail:
        msg += f"  —  {detail}"
    print(msg)


def info(label: str, detail: str = ""):
    msg = f"  [{INFO}]  {label}"
    if detail:
        msg += f"  —  {detail}"
    print(msg)


def add_fix(section: str, commands: list[str], note: str = ""):
    block = f"\n  ** {section} **\n"
    if note:
        block += f"     {note}\n"
    for cmd in commands:
        block += f"     > {cmd}\n"
    fixes.append(block)


# =============================================================================
# 1.  SYSTEM INFORMATION
# =============================================================================
def check_system():
    header("1 / 6  —  SYSTEM INFORMATION")
    info("OS", platform.platform())
    info("Python", sys.version.split()[0])
    info("Python path", sys.executable)
    info("Architecture", platform.machine())

    major, minor = sys.version_info[:2]
    ok = major == 3 and minor >= 10
    result("Python >= 3.10", ok, f"found {major}.{minor}")
    if not ok:
        add_fix(
            "Python version too old",
            [
                "Download Python 3.11+ from https://www.python.org/downloads/",
                "During install, CHECK 'Add Python to PATH'",
            ],
        )


# =============================================================================
# 2.  NVIDIA DRIVER & nvidia-smi
# =============================================================================
def check_nvidia_smi():
    header("2 / 6  —  NVIDIA DRIVER  (nvidia-smi)")

    nvsmi = shutil.which("nvidia-smi")
    if nvsmi is None:
        common = r"C:\Windows\System32\nvidia-smi.exe"
        if os.path.isfile(common):
            nvsmi = common

    if nvsmi is None:
        result("nvidia-smi found", False)
        add_fix(
            "nvidia-smi not found",
            [
                "Download the latest Game-Ready or Studio driver from:",
                "  https://www.nvidia.com/Download/index.aspx",
                "Install it, then reboot and re-run this test.",
            ],
        )
        return

    result("nvidia-smi found", True, nvsmi)

    try:
        out = subprocess.check_output(
            [nvsmi], stderr=subprocess.STDOUT, text=True, timeout=15
        )
        print()
        for line in out.strip().splitlines():
            print(f"    {line}")
        print()

        if "5090" in out:
            result("RTX 5090 detected", True)
        elif "NVIDIA" in out.upper():
            gpu_line = [l for l in out.splitlines() if "NVIDIA" in l.upper()]
            name = gpu_line[0].strip() if gpu_line else "unknown"
            warn("RTX 5090 NOT detected", f"found: {name}")
        else:
            result("RTX 5090 detected", False, "could not parse GPU name")

        if "CUDA Version" in out:
            for line in out.splitlines():
                if "CUDA Version" in line:
                    info("Driver CUDA version", line.strip())
                    break
    except FileNotFoundError:
        result("nvidia-smi executable", False, "file not found at runtime")
        add_fix(
            "nvidia-smi failed",
            [
                "Reinstall NVIDIA drivers from https://www.nvidia.com/Download/index.aspx",
                "Reboot after installation.",
            ],
        )
    except subprocess.TimeoutExpired:
        result("nvidia-smi responsive", False, "timed out after 15 s")
    except subprocess.CalledProcessError as exc:
        result("nvidia-smi execution", False, exc.output[:300])


# =============================================================================
# 3.  CUDA TOOLKIT (nvcc)
# =============================================================================
def check_cuda_toolkit():
    header("3 / 6  —  CUDA TOOLKIT  (nvcc)")

    nvcc = shutil.which("nvcc")
    if nvcc is None:
        warn(
            "nvcc not on PATH",
            "This is OK if PyTorch ships its own CUDA runtime (step 4 will confirm).",
        )
        add_fix(
            "CUDA Toolkit not found (optional but recommended)",
            [
                "Download CUDA Toolkit 12.4+ from:",
                "  https://developer.nvidia.com/cuda-downloads",
                "After install, ensure these are on your PATH:",
                r"  C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.x\bin",
                r"  C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.x\libnvvp",
            ],
            note="PyTorch bundles its own CUDA runtime, so this may not be required.",
        )
        return

    result("nvcc found", True, nvcc)
    try:
        out = subprocess.check_output(
            [nvcc, "--version"], stderr=subprocess.STDOUT, text=True, timeout=10
        )
        for line in out.strip().splitlines():
            if "release" in line.lower():
                info("CUDA Toolkit version", line.strip())
    except Exception as exc:
        warn("nvcc --version", str(exc)[:200])


# =============================================================================
# 4.  PyTorch + CUDA
# =============================================================================
def check_pytorch():
    header("4 / 6  —  PyTorch  +  CUDA")

    try:
        import torch
    except ImportError:
        result("import torch", False, "PyTorch is not installed")
        add_fix(
            "Install PyTorch with CUDA 12.4",
            [
                "pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124",
            ],
        )
        return

    result("import torch", True, f"version {torch.__version__}")

    cuda_available = torch.cuda.is_available()
    result("torch.cuda.is_available()", cuda_available)

    if not cuda_available:
        add_fix(
            "PyTorch cannot see CUDA",
            [
                "You may have the CPU-only build of PyTorch. Reinstall with CUDA:",
                "  pip uninstall torch torchvision torchaudio -y",
                "  pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124",
                "",
                "If that still fails, make sure your NVIDIA driver is up to date:",
                "  https://www.nvidia.com/Download/index.aspx",
            ],
        )
        return

    cuda_version = torch.version.cuda
    info("PyTorch CUDA version", cuda_version)

    cudnn = torch.backends.cudnn.version() if torch.backends.cudnn.is_available() else None
    if cudnn:
        info("cuDNN version", str(cudnn))
    else:
        warn("cuDNN", "not available — performance may be reduced")

    n_gpus = torch.cuda.device_count()
    info("GPU count", str(n_gpus))

    for i in range(n_gpus):
        name = torch.cuda.get_device_name(i)
        mem = torch.cuda.get_device_properties(i).total_memory / (1024 ** 3)
        cc = torch.cuda.get_device_properties(i).major
        result(
            f"GPU {i}",
            True,
            f"{name}  |  {mem:.1f} GB VRAM  |  Compute Capability {torch.cuda.get_device_properties(i).major}.{torch.cuda.get_device_properties(i).minor}",
        )

        if "5090" in name:
            result("RTX 5090 confirmed via PyTorch", True)

    info("Running quick tensor test on GPU...")
    try:
        t = torch.randn(2048, 2048, device="cuda")
        r = torch.mm(t, t)
        del t, r
        torch.cuda.synchronize()
        result("GPU tensor math", True, "matrix multiply successful")
    except Exception as exc:
        result("GPU tensor math", False, str(exc)[:200])
        add_fix(
            "GPU computation failed",
            [
                "This usually means a driver/toolkit mismatch. Try:",
                "  1) Update NVIDIA driver to the latest version",
                "  2) pip uninstall torch -y",
                "  3) pip install torch --index-url https://download.pytorch.org/whl/cu124",
            ],
        )


# =============================================================================
# 5.  faster-whisper (CTranslate2)
# =============================================================================
def check_faster_whisper():
    header("5 / 6  —  faster-whisper  (CTranslate2 engine)")

    try:
        import faster_whisper
    except ImportError:
        result("import faster_whisper", False, "not installed")
        add_fix(
            "Install faster-whisper",
            ["pip install faster-whisper"],
        )
        return

    version = getattr(faster_whisper, "__version__", "unknown")
    result("import faster_whisper", True, f"version {version}")

    try:
        import ctranslate2
        ct2_ver = getattr(ctranslate2, "__version__", "unknown")
        info("CTranslate2 version", ct2_ver)

        supported = ctranslate2.get_supported_compute_types("cuda")
        cuda_ok = "float16" in supported
        result("CTranslate2 CUDA support", cuda_ok, f"compute types: {', '.join(sorted(supported))}")
        if not cuda_ok:
            add_fix(
                "CTranslate2 has no CUDA support",
                [
                    "pip uninstall ctranslate2 -y",
                    "pip install ctranslate2",
                    "If that still fails, try:",
                    "  pip install ctranslate2 --extra-index-url https://download.pytorch.org/whl/cu128",
                ],
            )
    except ImportError:
        warn("ctranslate2 import", "could not import directly — faster-whisper may bundle it")
    except Exception as exc:
        warn("CTranslate2 CUDA check", str(exc)[:200])

    info("Attempting to load Whisper large-v3 (this downloads ~3 GB on first run)...")
    info("Model cache directory", os.path.join(os.path.dirname(__file__), "models"))
    print()

    try:
        from faster_whisper import WhisperModel

        model_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
        os.makedirs(model_dir, exist_ok=True)

        start = time.time()
        model = WhisperModel(
            "large-v3",
            device="cuda",
            compute_type="float16",
            download_root=model_dir,
        )
        elapsed = time.time() - start
        result("Whisper large-v3 loaded on GPU", True, f"{elapsed:.1f} s")

        import numpy as np
        dummy = np.zeros(16000, dtype=np.float32)
        info("Running dummy transcription (1 s silence)...")
        segments, seg_info = model.transcribe(dummy, language="en")
        _ = list(segments)
        result("Dummy transcription", True, "model is inference-ready")

        del model
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass

    except Exception as exc:
        result("Whisper large-v3 load", False, str(exc)[:300])
        add_fix(
            "Whisper model failed to load",
            [
                "Make sure steps 1-4 above all PASS first.",
                "Then try:",
                "  pip install --upgrade faster-whisper",
                "",
                "If you see an out-of-memory error, the model needs ~6 GB VRAM.",
                "The RTX 5090 has 32 GB so this should not be an issue.",
                "",
                "If you see a CUDA error, reinstall PyTorch + faster-whisper:",
                "  pip uninstall torch faster-whisper ctranslate2 -y",
                "  pip install torch --index-url https://download.pytorch.org/whl/cu124",
                "  pip install faster-whisper",
            ],
        )


# =============================================================================
# 6.  Supporting packages (quick import check)
# =============================================================================
def check_supporting_packages():
    header("6 / 6  —  SUPPORTING PACKAGES")

    packages = {
        "sounddevice": "Microphone capture",
        "numpy": "Audio array processing",
        "mss": "Screenshot capture",
        "PIL": "Image processing (Pillow)",
        "pytesseract": "OCR engine bridge",
        "win32gui": "Windows API (pywin32)",
        "keyboard": "Global hotkeys",
        "PyQt6.QtWidgets": "UI overlay framework",
        "pyqtgraph": "Waveform visualisation",
        "colorama": "Coloured terminal output",
    }

    missing = []
    for mod, desc in packages.items():
        try:
            __import__(mod)
            result(f"{mod}", True, desc)
        except ImportError:
            result(f"{mod}", False, f"{desc}  — not installed")
            missing.append(mod)

    tesseract_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.isfile(tesseract_path):
        result("Tesseract binary", True, tesseract_path)
    else:
        warn(
            "Tesseract binary not found",
            f"Expected at {tesseract_path}",
        )
        add_fix(
            "Install Tesseract OCR",
            [
                "Download the Windows installer from:",
                "  https://github.com/UB-Mannheim/tesseract/wiki",
                "Install to the default path: C:\\Program Files\\Tesseract-OCR\\",
                "After install, re-run this test.",
            ],
        )

    if missing:
        pip_names = {
            "PIL": "Pillow",
            "win32gui": "pywin32",
            "PyQt6.QtWidgets": "PyQt6",
        }
        install_names = [pip_names.get(m, m) for m in missing]
        add_fix(
            "Missing Python packages",
            [f"pip install {' '.join(install_names)}"],
        )


# =============================================================================
# FINAL REPORT
# =============================================================================
def final_report():
    print(f"\n{'=' * 72}")
    if all_passed and not fixes:
        print(f"  {Fore.GREEN}{BOLD}")
        print(f"  ALL CHECKS PASSED — Your RTX 5090 and Whisper v3 are ready!")
        print(f"  You can now run the full application with:  python main.py")
        print(f"  {RESET}")
    else:
        print(f"  {Fore.RED}{BOLD}")
        print(f"  SOME CHECKS FAILED — Follow the troubleshooting steps below.")
        print(f"  {RESET}")
        print(f"\n{THIN}")
        print(f"  {BOLD}TROUBLESHOOTING COMMANDS{RESET}")
        print(THIN)
        for fix in fixes:
            print(fix)
        print(THIN)
        print(f"\n  After running the commands above, re-run this test:")
        print(f"    python engine_test.py\n")
    print("=" * 72)


# =============================================================================
# ENTRY POINT
# =============================================================================
if __name__ == "__main__":
    print(f"\n{BOLD}{'=' * 72}")
    print(f"  WHISPER PROJECT  —  ENGINE TEST")
    print(f"  Verifying GPU, CUDA, and Whisper v3 readiness")
    print(f"{'=' * 72}{RESET}\n")

    check_system()
    check_nvidia_smi()
    check_cuda_toolkit()
    check_pytorch()
    check_faster_whisper()
    check_supporting_packages()
    final_report()
