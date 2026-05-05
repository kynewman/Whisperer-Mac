"""
Central configuration for the Whisper Project.
All tuneable settings live here so you never have to dig through other files.
"""

import os
import sys

VERSION = "5.5.7"
IS_MAC = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"

# =============================================================================
# Paths
# =============================================================================
PROJECT_ROOT = os.environ.get("WHISPERER_PROJECT_ROOT", os.path.dirname(os.path.abspath(__file__)))


def _default_model_cache_dir() -> str:
    if IS_MAC and (getattr(sys, "frozen", False) or ".app/Contents" in os.path.normpath(PROJECT_ROOT)):
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Whisperer", "models")
    if os.path.basename(PROJECT_ROOT).lower() == "_internal":
        if IS_WINDOWS:
            local_appdata = os.environ.get("LOCALAPPDATA")
            if local_appdata:
                return os.path.join(local_appdata, "Whisperer", "models")
    return os.path.join(PROJECT_ROOT, "models")


MODEL_CACHE_DIR = os.environ.get("WHISPERER_MODEL_CACHE_DIR", _default_model_cache_dir())

# =============================================================================
# Whisper Engine
# =============================================================================
WHISPER_MODEL_SIZE = "deepdml/faster-whisper-large-v3-turbo-ct2"
WHISPER_DEVICE = os.environ.get("WHISPERER_WHISPER_DEVICE", "cuda" if IS_WINDOWS else "cpu")
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPERER_WHISPER_COMPUTE_TYPE", "float16" if IS_WINDOWS else "int8")
WHISPER_BEAM_SIZE = 2  # Reduced from 5 for maximum speed (snappiness) without losing much accuracy
WHISPER_LANGUAGE = "en"

# =============================================================================
# Audio
# =============================================================================
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_DTYPE = "float32"
AUDIO_BLOCKSIZE = 1024

# =============================================================================
# Global Hotkey
# =============================================================================
DICTATION_HOTKEY = "ctrl+cmd" if IS_MAC else "ctrl+left windows"

# =============================================================================
# OCR / Context
# =============================================================================
TESSERACT_CMD = os.environ.get(
    "TESSERACT_CMD",
    "/opt/homebrew/bin/tesseract" if IS_MAC else r"C:\Program Files\Tesseract-OCR\tesseract.exe",
)
OCR_ENABLED = True

# =============================================================================
# UI Overlay
# =============================================================================
OVERLAY_WIDTH = 420
OVERLAY_HEIGHT = 116
OVERLAY_BOTTOM_MARGIN = 48
OVERLAY_OPACITY = 0.85
OVERLAY_BG_COLOR = (0, 0, 0, 0)
WAVEFORM_COLOR = (0, 180, 255)
WAVEFORM_ACTIVE_COLOR = (255, 255, 255)

# =============================================================================
# Formatting Rules — maps process-name substrings to rule module names
# =============================================================================
FORMATTING_RULES = {
    "outlook":      "rules.outlook",
    "resolve":      "rules.davinci",
    "writerduet":   "rules.writerduet",
}
DEFAULT_FORMATTING_RULE = "rules.outlook"
