"""File transcription workflow using ffmpeg and the existing pipeline."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Callable

import numpy as np

from core.transcriber import transcribe
from core.formatter import format_transcription
from core.dictionary import apply_replacements, get_prompt_words
from core.modes import get_mode
from core.history import save_dictation, save_context


SUPPORTED_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".m4a",
    ".mp4",
    ".mov",
    ".webm",
    ".mkv",
    ".aac",
    ".ogg",
    ".flac",
}


def is_supported(path: str) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS


def extract_audio(input_path: str) -> np.ndarray:
    """
    Use ffmpeg to extract audio as 16 kHz mono float32.
    Returns a 1-D numpy array.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-ar",
        "16000",
        "-ac",
        "1",
        "-f",
        "f32le",
        "pipe:1",
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.decode('utf-8', errors='ignore')[:200]}")
    audio = np.frombuffer(result.stdout, dtype=np.float32)
    if len(audio) == 0:
        raise RuntimeError("ffmpeg produced no audio output")
    return audio


def transcribe_file(
    input_path: str,
    mode_id: int | None = None,
    progress_callback: Callable[[float], None] | None = None,
) -> dict:
    """
    Run the full file transcription pipeline.

    Returns a dict with:
        dictation_id, raw_transcript, final_text, duration_s, mode_id, error
    """
    path = Path(input_path)
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {path.suffix}")

    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    t0 = time.time()

    if progress_callback:
        progress_callback(0.05)

    audio = extract_audio(input_path)
    duration_s = len(audio) / 16000.0

    if progress_callback:
        progress_callback(0.15)

    mode = get_mode(mode_id) if mode_id else None
    vocab_hints = get_prompt_words(80)

    raw_text = transcribe(audio, context_words=vocab_hints)

    if progress_callback:
        progress_callback(0.70)

    formatted = format_transcription(
        raw_text,
        active_app=path.suffix.lower(),
        window_title=path.name,
    )
    final_text = apply_replacements(formatted)

    if progress_callback:
        progress_callback(0.85)

    did = save_dictation(
        started_at=started_at,
        duration_ms=int(duration_s * 1000),
        app_name=path.name,
        window_title="",
        mode_id=mode_id,
        stt_provider="local",
        stt_model="file",
        raw_transcript=raw_text,
        final_text=final_text,
        replacements_applied=1,
    )

    if progress_callback:
        progress_callback(1.0)

    return {
        "dictation_id": did,
        "raw_transcript": raw_text,
        "final_text": final_text,
        "duration_s": duration_s,
        "mode_id": mode_id,
        "error": None,
    }
