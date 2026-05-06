"""
=============================================================================
  Whisper Project  —  Main Entry Point
=============================================================================
  A local, high-performance speech-to-text app for macOS and Windows.

  Usage:
      python main.py

  Hotkeys (configurable in Settings > Shortcuts):
      Dictation hotkey (hold)  — Quick dictation. Release to transcribe & paste.
      Dictation + Alt          — Long-form mode. Keeps recording after release.
      Toggle recording         — Start/stop recording without holding.
      Cancel                   — Discard current recording.
      Mode next / prev         — Cycle through enabled modes.
      Repeat last              — Paste the last dictation again.
=============================================================================
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import traceback


def _prefer_external_python_packages_for_installed_source() -> None:
    """
    Installed builds run the engine with system Python against files in
    ``_internal``. Keep that app code importable, but let real site-packages win
    over PyInstaller's partial bundled package folders.
    """
    app_root = os.environ.get("WHISPERER_PROJECT_ROOT") or os.path.dirname(os.path.abspath(__file__))
    if os.path.basename(app_root).lower() != "_internal":
        return
    try:
        app_root = os.path.normcase(os.path.abspath(app_root))
        moved = False
        next_path: list[str] = []
        for entry in sys.path:
            comparable = os.path.normcase(os.path.abspath(entry or os.curdir))
            if comparable == app_root:
                moved = True
                continue
            next_path.append(entry)
        if moved:
            next_path.append(app_root)
            sys.path[:] = next_path
    except Exception:
        pass


_prefer_external_python_packages_for_installed_source()

_PROCESS_START = time.perf_counter()

import config

_EARLY_MODEL_NAME = next(
    (arg.split("=", 1)[1] for arg in sys.argv[1:] if arg.startswith("--model=")),
    config.WHISPER_MODEL_SIZE,
)
if _EARLY_MODEL_NAME.lower().startswith("nvidia/parakeet"):
    import torch  # must be imported before PyQt6 to avoid c10.dll crash on Windows for NeMo/PyTorch
import numpy as np

from PyQt6.QtCore import QTimer, pyqtSignal, QObject
from PyQt6.QtWidgets import QApplication

from core.audio import AudioRecorder
from core.transcriber import (
    NVIDIA_RIVA_STREAMING_MODEL,
    NvidiaStreamingTranscriber,
    load_model,
    nvidia_riva_model_supports_streaming,
    nvidia_riva_streaming_model,
    prewarm_nvidia_riva,
    transcribe,
    warmup_model,
)
from core.context import (
    capture_clipboard_context,
    capture_screen_context,
    capture_screen_context_cached,
    capture_selected_text,
    capture_ui_automation_text,
    get_active_window_name,
    get_active_window_title,
    mark_clipboard_pasted,
)
from core.formatter import format_transcription, prepare_text_for_insertion
from core.dictionary import add_words_from_list, apply_replacements, get_prompt_words
from core.term_filter import extract_useful_terms
from core.modes import resolve_active_mode, list_modes, get_mode_by_name, seed_builtins
from core.history import save_dictation, save_context
from core.file_transcriber import transcribe_file
from core.settings import load_settings
from core import hotkeys
from core.output import paste_text
from core.perf import record_timing, timed
from core.audio_ducking import AudioDucker
from core.single_instance import acquire as acquire_single_instance
from ui.overlay import WaveformOverlay


def _normalize_keyboard_hotkey(hotkey: str | None) -> str | None:
    """Convert UI key names into names understood by the native hotkey backend."""
    return hotkeys.normalize_hotkey(hotkey)


STT_KEY_SERVICES = {
    "openai_whisper": "openai",
    "groq_whisper": "groq",
    "deepgram": "deepgram",
    "openai_compatible_stt": "openai_compat_stt",
    "nvidia_nim_parakeet": "nvidia",
}

STT_DEFAULT_MODELS = {
    "local": lambda: config.WHISPER_MODEL_SIZE,
    "openai_whisper": lambda: "gpt-4o-transcribe",
    "groq_whisper": lambda: "whisper-large-v3-turbo",
    "deepgram": lambda: "nova-3",
    "openai_compatible_stt": lambda: "whisper-large-v3",
    "nvidia_nim_parakeet": lambda: "parakeet-tdt-0.6b-v2",
}


def _stt_model_name(provider: str, override: str | None = None) -> str:
    override = (override or "").strip()
    if override:
        return override
    default = STT_DEFAULT_MODELS.get(provider)
    return default() if default else config.WHISPER_MODEL_SIZE


def _write_engine_ready_file(model_name: str) -> None:
    ready_file = os.environ.get("WHISPERER_ENGINE_READY_FILE")
    if not ready_file:
        return
    try:
        ready_dir = os.path.dirname(ready_file)
        if ready_dir:
            os.makedirs(ready_dir, exist_ok=True)
        with open(ready_file, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "pid": os.getpid(),
                    "model": model_name,
                    "readyAt": time.time(),
                },
                handle,
                separators=(",", ":"),
            )
    except Exception:
        pass

# Attempt to import Vosk for live recognition
try:
    from core.live_recognition import LiveRecognizer
    _VOSK_AVAILABLE = True
except Exception:
    _VOSK_AVAILABLE = False

ENGINE_FORCE_STOP_RESTART_CODE = 42
LONGFORM_POLL_INTERVAL_S = 0.004
LONGFORM_RELEASE_DEBOUNCE_S = 0.012
LONGFORM_LOCK_GRACE_S = 0.0
LONGFORM_STOP_ARM_DEBOUNCE_S = 0.06
DOUBLE_TAP_LOCK_WINDOW_S = 0.34
DOUBLE_TAP_MAX_FIRST_PRESS_S = 0.42
LOADING_PREVIEW_HIDE_S = 1.4


def _looks_silent(audio: np.ndarray, threshold: float = 0.0015) -> bool:
    if audio is None or len(audio) == 0:
        return True
    try:
        return float(np.sqrt(np.mean(audio.astype(np.float32, copy=False) ** 2))) < threshold
    except Exception:
        return False


class Signals(QObject):
    """Bridge between background threads and the Qt UI thread."""
    show_overlay = pyqtSignal()
    show_model_loading = pyqtSignal()
    hide_overlay = pyqtSignal()
    hide_overlay_now = pyqtSignal()
    set_active = pyqtSignal(bool)
    set_status = pyqtSignal(str)
    set_transcribed_text = pyqtSignal(str)
    set_mode = pyqtSignal(str)
    set_processing = pyqtSignal(bool)
    mode_changed = pyqtSignal(str)
    open_history = pyqtSignal()
    set_locked = pyqtSignal(bool)
    set_model_loading = pyqtSignal(bool)


class WhisperApp:
    """
    Orchestrates the full dictation workflow with configurable shortcuts,
    cancel behavior, mode cycling, and flexible output delivery.
    """

    def __init__(self):
        if not acquire_single_instance("WhispererEngine"):
            print("ENGINE_ALREADY_RUNNING", flush=True)
            raise SystemExit(0)
        mac_engine_accessory = (
            sys.platform == "darwin"
            and (
                os.environ.get("WHISPERER_ENGINE_PARENT_UI") == "1"
                or os.environ.get("WHISPERER_ENGINE_ACCESSORY") == "1"
            )
        )
        if mac_engine_accessory:
            try:
                from ui.macos_glass import set_macos_app_activation_policy

                set_macos_app_activation_policy(accessory=True)
            except Exception:
                pass
        self.app = QApplication(sys.argv)
        if mac_engine_accessory:
            try:
                from ui.macos_glass import set_macos_app_activation_policy

                set_macos_app_activation_policy(accessory=True)
            except Exception:
                pass
        self.overlay = WaveformOverlay()
        self.signals = Signals()
        self._context_words = ""
        self._running = True
        self._session_lock = threading.Lock()
        self._cancelled = False
        self._toggle_mode = False
        self._longform_requested = threading.Event()
        self._processing_job_active = threading.Event()
        self._model_ready = threading.Event()
        self._model_failed = ""
        self._loading_preview_visible = False
        self._loading_preview_lock = threading.Lock()
        self._pre_ready_hotkey_lock = threading.Lock()
        self._suppress_pre_ready_hotkey_until_release = False
        self._pre_ready_longform_requested = threading.Event()
        self._pending_active_target_lock = threading.Lock()
        self._pending_active_app = ""
        self._pending_window_title = ""
        self._audio_ducker: AudioDucker | None = None
        self._audio_ducker_lock = threading.Lock()
        self._audio_ducking_ticket = 0
        self._stdin_command_reader_started = False
        self._last_dictation_text = ""
        self._last_paste_target = ("", "")
        self._last_paste_needs_space = False
        self._dictation_press_started_at = 0.0
        self._last_mic_level_emit = 0.0
        self._registered_hotkeys: list = []
        self._hotkeys_paused = os.environ.get("WHISPERER_HOTKEYS_PAUSED") == "1"
        self._modes_list: list = []
        self._current_mode_index = 0
        seed_builtins()
        self._refresh_modes_list()

        # Word dictionary tracking
        self._recent_words = set()
        self._live_words = ""

        # Live Vosk recognizer. It is initialized after the main STT model is
        # ready so Vosk and NeMo never compete during startup imports.
        self._live_recognizer = None
        self.recorder = AudioRecorder(live_recognizer=None)
        self.app.aboutToQuit.connect(self.recorder.close)

        self.signals.show_overlay.connect(self.overlay.fade_in)
        self.signals.show_model_loading.connect(self.overlay.show_model_loading)
        self.signals.hide_overlay.connect(self.overlay.fade_out)
        self.signals.hide_overlay_now.connect(self.overlay.hide_now)
        self.signals.set_active.connect(self.overlay.set_active)
        self.signals.set_status.connect(self.overlay.set_status)
        self.signals.set_transcribed_text.connect(self.overlay.append_transcribed_text)
        self.signals.set_mode.connect(self.overlay.set_mode)
        self.signals.set_processing.connect(self.overlay.set_processing)
        self.signals.mode_changed.connect(self._on_mode_changed_overlay)
        self.signals.set_locked.connect(self.overlay.set_locked)
        self.signals.set_model_loading.connect(self.overlay.set_model_loading)
        self.overlay.open_ui_requested.connect(self._on_overlay_open_ui)
        self.overlay.force_stop_requested.connect(self._on_overlay_force_stop)

        self._feed_waveform = self._feed_waveform
        self._waveform_timer = QTimer()
        self._waveform_timer.timeout.connect(self._feed_waveform)
        self._waveform_timer.start(33)

    def _show_model_loading_overlay(self):
        if os.environ.get("WHISPERER_UI_LOADING_PREVIEW") == "1":
            return
        with self._loading_preview_lock:
            self._loading_preview_visible = True
        self.signals.set_processing.emit(False)
        self.signals.set_active.emit(False)
        self.signals.set_locked.emit(False)
        self.signals.set_status.emit("")
        self.signals.show_model_loading.emit()
        threading.Timer(LOADING_PREVIEW_HIDE_S, self._hide_loading_preview_quickly).start()

    def _mark_pre_ready_hotkey(self):
        with self._pre_ready_hotkey_lock:
            self._suppress_pre_ready_hotkey_until_release = True
        self._pre_ready_longform_requested.clear()

    def _clear_pre_ready_hotkey_after_release(self):
        dictation_hk = self._get_dictation_hotkey()
        while self._running and self._is_dictation_hotkey_pressed(dictation_hk):
            time.sleep(0.025)
        with self._pre_ready_hotkey_lock:
            self._suppress_pre_ready_hotkey_until_release = False
        self._pre_ready_longform_requested.clear()
        self._clear_pending_active_target()

    def _pre_ready_hotkey_still_held(self) -> bool:
        with self._pre_ready_hotkey_lock:
            suppress = self._suppress_pre_ready_hotkey_until_release
        if not suppress:
            return False
        if self._is_dictation_hotkey_pressed(self._get_dictation_hotkey()):
            return True
        if self._pre_ready_longform_requested.is_set():
            return True
        with self._pre_ready_hotkey_lock:
            self._suppress_pre_ready_hotkey_until_release = False
        self._pre_ready_longform_requested.clear()
        self._clear_pending_active_target()
        return False

    def _clear_pre_ready_hotkey_suppression(self):
        with self._pre_ready_hotkey_lock:
            self._suppress_pre_ready_hotkey_until_release = False
        self._pre_ready_longform_requested.clear()

    def _start_pre_ready_hotkey_dictation_if_held(self) -> bool:
        if not self._model_ready.is_set():
            return False
        longform_requested = self._pre_ready_longform_requested.is_set() or self._is_alt_pressed()
        hotkey_held = self._is_dictation_hotkey_pressed(self._get_dictation_hotkey())
        if not hotkey_held and not longform_requested:
            with self._pre_ready_hotkey_lock:
                self._suppress_pre_ready_hotkey_until_release = False
            return False
        if not self._session_lock.acquire(blocking=False):
            return False
        self._clear_pre_ready_hotkey_suppression()
        self._clear_longform_lock()
        active_app, window_title = self._consume_pending_active_target()
        if not active_app and not window_title:
            active_app, window_title = self._capture_active_target(include_title=False)
        self._prime_listening_overlay()
        if longform_requested:
            self._request_longform_lock()
        threading.Thread(
            target=lambda: self._run_one_dictation_session(
                lock_acquired=True,
                overlay_primed=True,
                initial_active_app=active_app,
                initial_window_title=window_title,
            ),
            daemon=True,
        ).start()
        return True

    def _hide_loading_preview_quickly(self):
        with self._loading_preview_lock:
            preview_visible = self._loading_preview_visible
            self._loading_preview_visible = False
        if (
            preview_visible
            and not self.recorder.is_recording
            and not self._session_lock.locked()
            and not self._processing_job_active.is_set()
        ):
            self.signals.hide_overlay.emit()

    def _hide_loading_overlay_if_idle(self):
        with self._loading_preview_lock:
            preview_visible = self._loading_preview_visible
            self._loading_preview_visible = False
        if (
            preview_visible
            and self._model_ready.is_set()
            and not self.recorder.is_recording
            and not self._session_lock.locked()
            and not self._processing_job_active.is_set()
        ):
            self.signals.hide_overlay.emit()

    def _on_live_word(self, text: str):
        """Callback for live word from Vosk."""
        if self._live_recognizer:
            self._live_words = text
            self.signals.set_transcribed_text.emit(text)

    def _ensure_live_recognizer(self):
        if self._live_recognizer or not _VOSK_AVAILABLE:
            return
        try:
            self._live_recognizer = LiveRecognizer(text_callback=self._on_live_word)
            self.recorder.live_recognizer = self._live_recognizer
        except Exception as exc:
            print(f"Live recognizer deferred initialization failed: {exc}", flush=True)

    def _feed_waveform(self):
        if self.recorder.is_recording:
            chunk = self.recorder.live_chunk
            self.overlay.set_audio_chunk(chunk)
            self._emit_mic_level(chunk)
        else:
            self.overlay.set_audio_chunk(None)

    def _emit_mic_level(self, chunk: np.ndarray | None):
        now = time.monotonic()
        if now - self._last_mic_level_emit < 0.12:
            return
        self._last_mic_level_emit = now
        if chunk is None or len(chunk) == 0:
            print("MIC_LEVEL -96.0 0.0000", flush=True)
            return
        rms = float(np.sqrt(np.mean(chunk.astype(np.float32, copy=False) ** 2)))
        db = 20.0 * float(np.log10(max(rms, 1e-7)))
        level = max(0.0, min(1.0, (db + 60.0) / 52.0))
        print(f"MIC_LEVEL {db:.1f} {level:.4f}", flush=True)

    def _refresh_modes_list(self):
        """Reload enabled modes for cycling."""
        self._modes_list = list_modes(enabled_only=True)
        if not self._modes_list:
            self._modes_list = [get_mode_by_name("Voice") or resolve_active_mode()]

    def _capture_active_target(self, *, include_title: bool = True) -> tuple[str, str]:
        try:
            active_app = get_active_window_name()
            window_title = get_active_window_title() if include_title else ""
            return active_app, window_title
        except Exception:
            return "", ""

    def _store_pending_active_target(self, active_app: str = "", window_title: str = "") -> tuple[str, str]:
        if not active_app and not window_title:
            active_app, window_title = self._capture_active_target(include_title=False)
        with self._pending_active_target_lock:
            self._pending_active_app = active_app or ""
            self._pending_window_title = window_title or ""
        if active_app or window_title:
            print(f"DICTATION_TARGET_CAPTURED app={active_app!r} title={window_title!r}", flush=True)
        return active_app, window_title

    def _consume_pending_active_target(self) -> tuple[str, str]:
        with self._pending_active_target_lock:
            active_app = self._pending_active_app
            window_title = self._pending_window_title
            self._pending_active_app = ""
            self._pending_window_title = ""
        return active_app, window_title

    def _clear_pending_active_target(self) -> None:
        with self._pending_active_target_lock:
            self._pending_active_app = ""
            self._pending_window_title = ""

    def _begin_active_target_capture(self) -> tuple[dict[str, str], threading.Thread]:
        result = {"active_app": "", "window_title": ""}

        def _capture():
            active_app, window_title = self._capture_active_target()
            result["active_app"] = active_app
            result["window_title"] = window_title

        thread = threading.Thread(target=_capture, daemon=True)
        thread.start()
        return result, thread

    def _finish_active_target_capture(
        self,
        result: dict[str, str] | None,
        thread: threading.Thread | None,
        *,
        timeout: float = 0.25,
        fallback: bool = True,
    ) -> tuple[str, str]:
        if thread is not None:
            thread.join(timeout=timeout)
        active_app = (result or {}).get("active_app", "")
        window_title = (result or {}).get("window_title", "")
        if active_app or window_title:
            return active_app, window_title
        if not fallback:
            return "", ""
        return self._capture_active_target()

    def _needs_prompt_context(self, mode, context_mode: str, stt_provider: str | None = None) -> bool:
        if context_mode == "off":
            return False
        provider = (stt_provider or getattr(mode, "stt_provider", None) or "local").strip() or "local"
        if getattr(mode, "llm_enabled", False):
            return True
        if provider in {"nvidia_nim_parakeet", "deepgram"}:
            return False
        parakeet_local = (
            provider == "local"
            and config.WHISPER_MODEL_SIZE.lower().startswith("nvidia/parakeet")
        )
        return not parakeet_local

    def _begin_context_prefetch(
        self,
        mode,
        settings: dict,
        active_app: str,
        stt_provider: str | None = None,
    ) -> dict:
        perf_cfg = settings.get("performance", {})
        context_mode = str(perf_cfg.get("context_mode", "fast")).lower()
        prefetch = {
            "mode": context_mode,
            "results": {},
            "threads": [],
            "vocab_thread": None,
            "vocab": "",
        }
        if not self._needs_prompt_context(mode, context_mode, stt_provider=stt_provider):
            return prefetch

        results = prefetch["results"]

        def _collect(name: str, fn):
            try:
                with timed(f"context_{name}"):
                    results[name] = fn()
            except Exception:
                results[name] = ""

        def _start(name: str, fn):
            thread = threading.Thread(target=lambda: _collect(name, fn), daemon=True)
            prefetch["threads"].append(thread)
            thread.start()

        full_context = context_mode == "full"
        if mode.ctx_ocr:
            ocr_fn = capture_screen_context if full_context else lambda: capture_screen_context_cached(blocking=False)
            _start("ocr", ocr_fn)
        if mode.ctx_selected_text and full_context:
            _start("selected_text", capture_selected_text)
        if mode.ctx_clipboard:
            _start("clipboard", capture_clipboard_context)
        if context_mode != "off":
            _start("ui_automation", lambda: capture_ui_automation_text(app_name=active_app))

        try:
            vocab_limit = int(settings.get("dictation", {}).get("vocabulary_prompt_limit", 80))
        except (TypeError, ValueError):
            vocab_limit = 80
        if vocab_limit > 0:
            def _collect_vocab():
                try:
                    with timed("dictionary_prompt"):
                        prefetch["vocab"] = get_prompt_words(vocab_limit)
                except Exception:
                    prefetch["vocab"] = ""

            vocab_thread = threading.Thread(target=_collect_vocab, daemon=True)
            prefetch["vocab_thread"] = vocab_thread
            vocab_thread.start()
        return prefetch

    def _finish_context_prefetch(self, prefetch: dict, *, wait_s: float) -> tuple[dict[str, str], str]:
        deadline = time.time() + max(0.0, wait_s)
        for thread in prefetch.get("threads", []):
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)
        vocab_thread = prefetch.get("vocab_thread")
        if vocab_thread is not None:
            remaining = deadline - time.time()
            if remaining > 0:
                vocab_thread.join(timeout=remaining)
        results = dict(prefetch.get("results", {}))
        vocab = str(prefetch.get("vocab", "") or "")
        return results, vocab

    def _start_streaming_stt_if_available(
        self,
        stt_provider: str,
        stt_model: str,
        mode,
        settings: dict,
    ):
        perf_cfg = settings.get("performance", {})
        if not bool(perf_cfg.get("streaming_stt_enabled", True)):
            return None
        if stt_provider != "nvidia_nim_parakeet":
            return None
        if not nvidia_riva_model_supports_streaming(stt_model):
            print(f"STREAMING_STT_SKIPPED model={stt_model!r} offline_only=True", flush=True)
            return None
        streaming_model = nvidia_riva_streaming_model(stt_model)
        from core.secrets import get_key

        key = get_key("nvidia")
        if not key:
            return None
        session = NvidiaStreamingTranscriber(
            key,
            base_url=settings.get("stt", {}).get("nvidia_nim_url", ""),
            language=mode.language or config.WHISPER_LANGUAGE,
            model=streaming_model,
            text_callback=lambda text: self.signals.set_transcribed_text.emit(text),
        )
        try:
            session.start()
            self.recorder.add_audio_consumer(session)
            print(
                f"STREAMING_STT_STARTED provider='nvidia_nim_parakeet' model={streaming_model!r} selected_model={stt_model!r}",
                flush=True,
            )
            return session
        except Exception as exc:
            print(f"STREAMING_STT_UNAVAILABLE {exc}", flush=True)
            return None

    def _finish_streaming_stt(self, session, wait_s: float = 0.75) -> str:
        if session is None:
            return ""
        self.recorder.remove_audio_consumer(session)
        with timed("transcribe_streaming_finalize"):
            text = session.finish(timeout_s=wait_s)
        error = getattr(session, "error", "")
        if error:
            print(f"STREAMING_STT_FAILED {error}", flush=True)
        if text:
            print(f"STREAMING_STT_READY chars={len(text)}", flush=True)
        return text

    def _streaming_finalize_timeout(self, session, perf_cfg: dict, audio_duration_s: float) -> float:
        try:
            max_ms = int(perf_cfg.get("streaming_finalize_wait_ms", 450))
        except (TypeError, ValueError):
            max_ms = 450
        max_wait_s = max(0.05, max_ms / 1000.0)
        if not bool(perf_cfg.get("streaming_adaptive_finalize_enabled", True)):
            return max_wait_s
        try:
            fast_ms = int(perf_cfg.get("streaming_fast_finalize_wait_ms", 120))
        except (TypeError, ValueError):
            fast_ms = 120
        adaptive = getattr(session, "adaptive_finalize_timeout", None)
        if not callable(adaptive):
            return max_wait_s
        wait_s = max(0.03, float(adaptive(max_wait_s, max(0.03, fast_ms / 1000.0))))
        print(
            f"STREAMING_STT_FINALIZE_WAIT wait={wait_s * 1000:.0f}ms max={max_wait_s * 1000:.0f}ms duration={audio_duration_s:.2f}s",
            flush=True,
        )
        return wait_s

    def _capture_recording_tail(self, settings: dict) -> None:
        try:
            tail_ms = int(settings.get("performance", {}).get("streaming_tail_capture_ms", 90))
        except (TypeError, ValueError):
            tail_ms = 90
        tail_ms = max(0, min(180, tail_ms))
        if tail_ms <= 0:
            return
        with timed("recording_tail_capture"):
            time.sleep(tail_ms / 1000.0)

    def _usable_streaming_text(self, text: str, audio_duration_s: float) -> bool:
        cleaned = (text or "").strip()
        if not cleaned:
            return False
        # A one-character final is usually an abandoned partial from hosted
        # streaming. Fall back to the normal provider request instead of
        # pasting junk over a perfectly good dictation.
        if len(cleaned) < 2:
            return False
        if not any(ch.isalnum() for ch in cleaned):
            return False
        if audio_duration_s >= 1.5 and len(cleaned) < 4 and " " not in cleaned:
            return False
        return True

    def _prewarm_cloud_stt_clients(self, settings: dict) -> None:
        """Hide cloud SDK imports/client setup behind startup idle time."""
        try:
            perf_cfg = settings.get("performance", {})
            if not bool(perf_cfg.get("streaming_stt_enabled", True)):
                return
            from core.secrets import get_key

            key = get_key("nvidia")
            if not key:
                return
            models = {
                _stt_model_name("nvidia_nim_parakeet", mode.stt_model)
                for mode in list_modes(enabled_only=True)
                if (mode.stt_provider or "") == "nvidia_nim_parakeet"
            }
            if not models:
                models.add(_stt_model_name("nvidia_nim_parakeet"))
            models.add(NVIDIA_RIVA_STREAMING_MODEL)
            warmed = prewarm_nvidia_riva(
                key,
                base_url=settings.get("stt", {}).get("nvidia_nim_url", ""),
                models=models,
            )
            if warmed:
                print(f"NVIDIA_RIVA_PREWARM_READY models={warmed}", flush=True)
        except Exception as exc:
            print(f"NVIDIA_RIVA_PREWARM_SKIPPED {exc}", flush=True)

    def _wait_until_engine_idle(self, idle_s: float = 6.0, max_wait_s: float = 90.0) -> bool:
        deadline = time.monotonic() + max_wait_s
        idle_started = 0.0
        while self._running and time.monotonic() < deadline:
            busy = self.recorder.is_recording or self._session_lock.locked() or self._processing_job_active.is_set()
            now = time.monotonic()
            if not busy:
                if idle_started <= 0:
                    idle_started = now
                if now - idle_started >= idle_s:
                    return True
            else:
                idle_started = 0.0
            time.sleep(0.2)
        return self._running

    def _warm_local_model_when_idle(self, model_name: str, settings: dict) -> None:
        perf_cfg = settings.get("performance", {})
        if perf_cfg.get("engine_preload", "app_start") == "off":
            print("LOCAL_MODEL_WARMUP_SKIPPED preload_off", flush=True)
            return
        if not self._wait_until_engine_idle():
            return
        engine_name = "NVIDIA Parakeet" if model_name.lower().startswith("nvidia/parakeet") else "Whisper"
        target = "GPU" if config.WHISPER_DEVICE == "cuda" else config.WHISPER_DEVICE.upper()
        print(f"Warming {engine_name} local fallback on {target}...", flush=True)
        try:
            with timed("engine_startup_model_phase"):
                load_model()
                warmup_model()
            print(f"LOCAL_MODEL_READY {model_name}", flush=True)
        except Exception as exc:
            self._model_failed = str(exc)
            print(f"LOCAL_MODEL_WARMUP_FAILED {exc}", flush=True)

    def _on_mode_changed_overlay(self, mode_name: str):
        """Show a brief mode-change notification in the overlay."""
        self.overlay.set_mode(mode_name)
        self.signals.show_overlay.emit()
        self.signals.set_status.emit(f"Mode: {mode_name}")
        QTimer.singleShot(1200, self.signals.hide_overlay.emit)

    def _on_overlay_open_ui(self):
        print("OPEN_UI_REQUESTED", flush=True)
        if os.environ.get("WHISPERER_ENGINE_PARENT_UI") == "1":
            if sys.platform == "darwin":
                try:
                    subprocess.Popen(
                        ["open", "-b", "com.whisperer.app"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except Exception:
                    pass
            return
        if self._show_launcher_window():
            return
        try:
            launcher_path = os.path.join(config.PROJECT_ROOT, "launcher.py")
            subprocess.Popen(
                [sys.executable, launcher_path],
                cwd=config.PROJECT_ROOT,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
        except Exception:
            pass

    def _show_launcher_window(self) -> bool:
        if os.name != "nt":
            return False
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            hwnd_match = wintypes.HWND()

            enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            @enum_proc_type
            def enum_proc(hwnd, lparam):
                if not user32.IsWindow(hwnd):
                    return True
                is_match = False
                length = user32.GetWindowTextLengthW(hwnd)
                title = ""
                if length > 0:
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buffer, length + 1)
                    title = buffer.value
                    is_match = title.startswith("Whisperer v")
                if not is_match:
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if pid.value:
                        try:
                            import psutil

                            proc = psutil.Process(pid.value)
                            cmdline = " ".join(proc.cmdline()).lower()
                            is_match = "launcher.py" in cmdline and "whisperer" in cmdline
                        except Exception:
                            is_match = False
                if is_match:
                    hwnd_match.value = hwnd
                    return False
                return True

            user32.EnumWindows(enum_proc, 0)
            if not hwnd_match.value:
                return False
            user32.ShowWindow(hwnd_match.value, 5)  # SW_SHOW
            user32.ShowWindow(hwnd_match.value, 9)  # SW_RESTORE
            user32.SetWindowPos(hwnd_match.value, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
            user32.SetWindowPos(hwnd_match.value, -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
            user32.SetForegroundWindow(hwnd_match.value)
            return True
        except Exception:
            return False

    def _on_overlay_force_stop(self):
        print("STOP_DICTATION_REQUESTED", flush=True)
        self._cancelled = True
        self._clear_longform_lock()
        self.signals.set_locked.emit(False)
        self.signals.set_active.emit(False)
        if self.recorder.is_recording:
            self.signals.set_status.emit("Stopping...")
            return
        if self._processing_job_active.is_set() or self._session_lock.locked():
            self.signals.set_status.emit("Stopping...")
            return
        self.signals.set_processing.emit(False)
        self.signals.hide_overlay.emit()

    def _prime_listening_overlay(self):
        print("DICTATION_STARTED", flush=True)
        self.signals.set_model_loading.emit(False)
        self.signals.set_processing.emit(False)
        self.signals.show_overlay.emit()
        if not self._longform_requested.is_set():
            self.signals.set_locked.emit(False)
        self.signals.set_active.emit(True)
        self.signals.set_status.emit("Listening...")

    def _request_longform_lock(self):
        self._longform_requested.set()
        self.signals.set_locked.emit(True)

    def _clear_longform_lock(self):
        self._longform_requested.clear()

    def _get_dictation_hotkey(self) -> str:
        settings = load_settings()
        hotkey = settings.get("shortcuts", {}).get("dictation") or config.DICTATION_HOTKEY
        return _normalize_keyboard_hotkey(hotkey) or config.DICTATION_HOTKEY

    def _is_dictation_hotkey_pressed(self, dictation_hk: str) -> bool:
        try:
            if hotkeys.is_pressed(dictation_hk):
                return True
        except Exception:
            pass
        parts = [part.strip() for part in dictation_hk.split("+") if part.strip()]
        if not parts:
            return False
        try:
            return all(hotkeys.is_pressed(part) for part in parts)
        except Exception:
            return False

    def _longform_lock_requested(self) -> bool:
        return self._longform_requested.is_set() or self._is_alt_pressed()

    def _wait_for_release_or_longform(self, dictation_hk: str) -> bool | None:
        """
        Poll while the hotkey is held. Returns True for longform, False for quick mode,
        None if cancelled.
        """
        time.sleep(0.002)
        started_monotonic = self._dictation_press_started_at or time.monotonic()
        release_seen_at: float | None = None
        release_grace_until: float | None = None
        double_tap_grace_until: float | None = None
        while True:
            if self._cancelled:
                return None
            if self._longform_lock_requested():
                self._request_longform_lock()
                return True
            if self._is_dictation_hotkey_pressed(dictation_hk):
                release_seen_at = None
                release_grace_until = None
                if double_tap_grace_until is not None:
                    self._request_longform_lock()
                    return True
                time.sleep(LONGFORM_POLL_INTERVAL_S)
            else:
                now = time.monotonic()
                if release_seen_at is None:
                    release_seen_at = now
                    release_grace_until = now + LONGFORM_LOCK_GRACE_S
                    if now - started_monotonic <= DOUBLE_TAP_MAX_FIRST_PRESS_S:
                        double_tap_grace_until = now + DOUBLE_TAP_LOCK_WINDOW_S
                if now - release_seen_at < LONGFORM_RELEASE_DEBOUNCE_S:
                    time.sleep(LONGFORM_POLL_INTERVAL_S)
                    continue
                if double_tap_grace_until is not None and now < double_tap_grace_until:
                    if self._longform_lock_requested():
                        self._request_longform_lock()
                        return True
                    time.sleep(LONGFORM_POLL_INTERVAL_S)
                    continue
                if release_grace_until is not None and now < release_grace_until:
                    time.sleep(LONGFORM_POLL_INTERVAL_S)
                    continue
                if self._longform_lock_requested():
                    self._request_longform_lock()
                    return True
                return False

    def _is_alt_pressed(self) -> bool:
        for key in ("alt", "left alt", "right alt", "menu"):
            try:
                if hotkeys.is_pressed(key):
                    return True
            except Exception:
                continue
        return False

    def _wait_for_longform_stop(self, dictation_hk: str):
        """In long-form mode, wait for the user to press the dictation hotkey again or cancel."""
        released_at: float | None = None
        while not self._cancelled:
            if self._is_dictation_hotkey_pressed(dictation_hk):
                released_at = None
            else:
                if released_at is None:
                    released_at = time.time()
                if time.time() - released_at >= LONGFORM_STOP_ARM_DEBOUNCE_S:
                    break
            time.sleep(0.02)

        while not self._cancelled:
            if self._is_dictation_hotkey_pressed(dictation_hk):
                time.sleep(0.05)
                while self._is_dictation_hotkey_pressed(dictation_hk):
                    time.sleep(0.02)
                return
            time.sleep(0.02)

    def _wait_for_toggle_stop(self, dictation_hk: str):
        """In toggle mode, wait for toggle hotkey again or cancel."""
        while not self._cancelled:
            if self._is_dictation_hotkey_pressed(dictation_hk):
                time.sleep(0.05)
                while self._is_dictation_hotkey_pressed(dictation_hk):
                    time.sleep(0.02)
                return
            time.sleep(0.02)

    def _on_alt_lock_pressed(self):
        """Request long-form mode whenever Alt is pressed during an active dictation session."""
        if not self._model_ready.is_set():
            with self._pre_ready_hotkey_lock:
                waiting_for_ready = self._suppress_pre_ready_hotkey_until_release
            if waiting_for_ready and self._is_dictation_hotkey_pressed(self._get_dictation_hotkey()):
                self._pre_ready_longform_requested.set()
                self.signals.set_locked.emit(True)
            return
        if self._model_ready.is_set() and self._pre_ready_hotkey_still_held():
            self._clear_pre_ready_hotkey_suppression()
            if not self._session_lock.acquire(blocking=False):
                self._request_longform_lock()
                return
            self._clear_longform_lock()
            self._request_longform_lock()
            active_app, window_title = self._consume_pending_active_target()
            if not active_app and not window_title:
                active_app, window_title = self._capture_active_target(include_title=False)
            self._prime_listening_overlay()
            threading.Thread(
                target=lambda: self._run_one_dictation_session(
                    lock_acquired=True,
                    overlay_primed=True,
                    initial_active_app=active_app,
                    initial_window_title=window_title,
                ),
                daemon=True,
            ).start()
            return
        if self._session_lock.locked() or self.recorder.is_recording:
            self._request_longform_lock()

    def _resolve_paste_method(self, active_app: str, mode) -> tuple[str, bool, bool, int, bool]:
        """
        Determine paste method, restore_clipboard, and auto_send for the current app.
        Priority: per-app override > mode setting > global setting.
        """
        settings = load_settings()
        paste_cfg = settings.get("paste", {})
        perf_cfg = settings.get("performance", {})

        method = mode.paste_method if mode.paste_method else paste_cfg.get("method", "clipboard_paste")
        restore = paste_cfg.get("restore_clipboard", False)
        auto_send = mode.auto_send if mode.auto_send else paste_cfg.get("auto_send_enter", False)
        paste_delay = int(perf_cfg.get("paste_delay_ms", 30))
        fast_path = bool(perf_cfg.get("paste_fast_path_enabled", True))

        # Per-app overrides
        overrides = paste_cfg.get("per_app_overrides", {})
        active_lower = active_app.lower()
        for app_substring, override in overrides.items():
            if app_substring.lower() in active_lower:
                if "method" in override:
                    method = override["method"]
                if "restore_clipboard" in override:
                    restore = override["restore_clipboard"]
                if "auto_send_enter" in override:
                    auto_send = override["auto_send_enter"]
                if "fast_path" in override:
                    fast_path = bool(override["fast_path"])
                break

        delay_overrides = perf_cfg.get("paste_delay_overrides", {})
        for app_substring, delay in delay_overrides.items():
            if app_substring.lower() in active_lower:
                try:
                    paste_delay = int(delay)
                except (TypeError, ValueError):
                    pass
                break

        if fast_path:
            fast_all_apps = bool(perf_cfg.get("paste_fast_all_apps", True))
            if not fast_all_apps:
                fast_apps = perf_cfg.get("paste_fast_apps", [])
                if not isinstance(fast_apps, list):
                    fast_apps = []
                fast_path = any(str(app).strip().lower() in active_lower for app in fast_apps if str(app).strip())
            if method != "clipboard_paste" or restore:
                fast_path = False
            if fast_path:
                try:
                    paste_delay = min(paste_delay, max(0, int(perf_cfg.get("paste_fast_delay_ms", 12))))
                except (TypeError, ValueError):
                    paste_delay = min(paste_delay, 12)

        return method, restore, auto_send, paste_delay, fast_path

    def _save_dictation_background(
        self,
        started_at: str,
        duration_ms: int,
        active_app: str,
        window_title: str,
        mode_id: int | None,
        raw_text: str,
        final_text: str,
        contexts: dict[str, str],
        audio_path: str | None = None,
        error: str | None = None,
        stt_provider: str = "local",
        stt_model: str | None = None,
        llm_processed: int = 0,
        paste_method: str = "clipboard_paste",
        paste_succeeded: int = 0,
    ):
        settings = load_settings()
        if not settings.get("privacy", {}).get("retain_history", True):
            return
        try:
            did = save_dictation(
                started_at=started_at,
                duration_ms=duration_ms,
                app_name=active_app,
                window_title=window_title,
                mode_id=mode_id,
                stt_provider=stt_provider,
                stt_model=stt_model or _stt_model_name(stt_provider),
                raw_transcript=raw_text,
                final_text=final_text,
                replacements_applied=1,
                llm_processed=llm_processed,
                paste_method=paste_method,
                paste_succeeded=paste_succeeded,
                error=error,
                audio_path=audio_path,
            )
            for source, content in contexts.items():
                if content:
                    save_context(did, source, content)
        except Exception:
            pass

    def _next_audio_ducking_ticket(self) -> int:
        with self._audio_ducker_lock:
            self._audio_ducking_ticket += 1
            return self._audio_ducking_ticket

    def _begin_audio_ducking(self, ticket: int | None = None):
        with self._audio_ducker_lock:
            if ticket is not None and ticket != self._audio_ducking_ticket:
                return
            if self._audio_ducker is not None:
                return

        ducker = AudioDucker.from_settings(load_settings())
        ducker.duck()

        with self._audio_ducker_lock:
            should_restore = (
                (ticket is not None and ticket != self._audio_ducking_ticket)
                or self._audio_ducker is not None
            )
            if not should_restore:
                self._audio_ducker = ducker

        if should_restore:
            ducker.restore()

    def _restore_audio_ducking(self):
        with self._audio_ducker_lock:
            self._audio_ducking_ticket += 1
            ducker = self._audio_ducker
            self._audio_ducker = None
        if ducker is not None:
            ducker.restore()

    def _run_one_dictation_session(
        self,
        toggle_mode: bool = False,
        lock_acquired: bool = False,
        overlay_primed: bool = False,
        initial_active_app: str = "",
        initial_window_title: str = "",
    ):
        """Run a single dictation: show overlay, record, transcribe, paste. Runs in a background thread."""
        acquired_lock = lock_acquired
        if not acquired_lock:
            acquired_lock = self._session_lock.acquire(blocking=False)
            if not acquired_lock:
                return
        self._cancelled = False
        if self._is_alt_pressed():
            self._request_longform_lock()
        self._toggle_mode = toggle_mode
        started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        t0 = time.time()
        active_app = initial_active_app or ""
        window_title = initial_window_title or ""
        mode_id = None
        mode = None
        contexts: dict[str, str] = {}
        stt_provider = "local"
        stt_model = _stt_model_name(stt_provider)
        settings = load_settings()
        context_prefetch: dict | None = None
        streaming_session = None
        streaming_provider = ""
        streaming_model = ""
        active_capture: dict[str, str] | None = None
        active_capture_thread: threading.Thread | None = None

        try:
            if not self._running:
                return
            if not active_app and not window_title:
                active_app, window_title = self._capture_active_target(include_title=False)
            if not window_title:
                active_capture, active_capture_thread = self._begin_active_target_capture()
            try:
                mode = resolve_active_mode(active_app, window_title)
            except Exception:
                mode = resolve_active_mode()
            mode_id = mode.id
            self.signals.set_mode.emit(mode.name)
            stt_provider = mode.stt_provider or "local"
            stt_model = _stt_model_name(stt_provider, mode.stt_model)
            if not overlay_primed:
                self._prime_listening_overlay()

            try:
                with timed("recorder_start"):
                    self.recorder.refresh_settings(settings)
                    streaming_session = self._start_streaming_stt_if_available(
                        stt_provider,
                        stt_model,
                        mode,
                        settings,
                    )
                    if streaming_session is not None:
                        streaming_provider = stt_provider
                        streaming_model = getattr(streaming_session, "model", stt_model)
                    self.recorder.start()
                    context_prefetch = self._begin_context_prefetch(
                        mode,
                        settings,
                        active_app,
                        stt_provider=stt_provider,
                    )
                if self._is_alt_pressed():
                    self._request_longform_lock()
            except Exception as exc:
                self.signals.set_active.emit(False)
                self.signals.set_locked.emit(False)
                self.signals.set_status.emit(f"Mic error: {exc}")
                time.sleep(1.2)
                self.signals.hide_overlay.emit()
                return

            if self._live_recognizer:
                self._live_recognizer.start()

            duck_ticket = self._next_audio_ducking_ticket()
            threading.Thread(target=lambda: self._begin_audio_ducking(duck_ticket), daemon=True).start()

            dictation_hk = self._get_dictation_hotkey()

            if toggle_mode:
                self.signals.set_status.emit("Toggle recording  —  Press toggle to finish")
                self._wait_for_toggle_stop(dictation_hk)
            else:
                longform = self._wait_for_release_or_longform(dictation_hk)
                if longform is None:  # cancelled
                    with timed("recorder_stop_cancelled"):
                        audio = self.recorder.stop()
                    self._finish_streaming_stt(streaming_session, wait_s=0.05)
                    streaming_session = None
                    self._restore_audio_ducking()
                    self.signals.set_active.emit(False)
                    self.signals.set_locked.emit(False)
                    self.signals.set_processing.emit(False)
                    if self._live_recognizer:
                        self._live_recognizer.stop()
                    duration_ms = int((time.time() - t0) * 1000)
                    if duration_ms < 1000:
                        self.signals.set_status.emit("Cancelled.")
                    else:
                        self.signals.set_status.emit("Recording cancelled.")
                    time.sleep(0.8)
                    self.signals.hide_overlay.emit()
                    return
                if longform:
                    self.signals.set_locked.emit(True)
                    self.signals.set_status.emit("Long-form mode  —  Press dictation hotkey to finish")
                    self._wait_for_longform_stop(dictation_hk)

            self.signals.set_active.emit(False)
            self.signals.set_locked.emit(False)
            self.signals.set_status.emit("Transcribing...")
            self.signals.set_processing.emit(True)

            if self._cancelled:
                with timed("recorder_stop_cancelled"):
                    audio = self.recorder.stop()
                self._finish_streaming_stt(streaming_session, wait_s=0.05)
                streaming_session = None
                print("MIC_LEVEL -96.0 0.0000", flush=True)
                self._restore_audio_ducking()
                self.signals.set_active.emit(False)
                self.signals.set_locked.emit(False)
                self.signals.set_processing.emit(False)
                if self._live_recognizer:
                    self._live_recognizer.stop()
                duration_ms = int((time.time() - t0) * 1000)
                if duration_ms < 1000:
                    self.signals.set_status.emit("Cancelled.")
                else:
                    self.signals.set_status.emit("Recording cancelled.")
                time.sleep(0.8)
                self.signals.hide_overlay.emit()
                return

            self._capture_recording_tail(settings)
            with timed("recorder_stop"):
                audio = self.recorder.stop()
            print("MIC_LEVEL -96.0 0.0000", flush=True)
            self._restore_audio_ducking()

            if self._live_recognizer:
                self._live_recognizer.stop()

            if active_capture_thread is not None:
                captured_app, captured_title = self._finish_active_target_capture(
                    active_capture,
                    active_capture_thread,
                    timeout=0.03,
                    fallback=False,
                )
                if captured_app and not active_app:
                    active_app = captured_app
                if captured_title:
                    window_title = captured_title
                    try:
                        updated_mode = resolve_active_mode(active_app, window_title)
                        if updated_mode.id != mode_id:
                            mode = updated_mode
                            mode_id = mode.id
                            self.signals.set_mode.emit(mode.name)
                            stt_provider = mode.stt_provider or "local"
                            stt_model = _stt_model_name(stt_provider, mode.stt_model)
                    except Exception:
                        pass

            duration_ms = int((time.time() - t0) * 1000)

            if len(audio) < config.AUDIO_SAMPLE_RATE * 0.3 or _looks_silent(audio):
                self._finish_streaming_stt(streaming_session, wait_s=0.05)
                streaming_session = None
                self.signals.set_processing.emit(False)
                self.signals.set_status.emit("No speech detected.")
                time.sleep(0.3)
                self.signals.hide_overlay.emit()
                return

            self._processing_job_active.set()
            perf_cfg = settings.get("performance", {})
            context_mode = str(perf_cfg.get("context_mode", "fast")).lower()
            self.signals.set_status.emit("Transcribing...")
            context_budget = 0.35 if context_mode == "full" else 0.015
            if context_prefetch is None:
                context_prefetch = self._begin_context_prefetch(
                    mode,
                    settings,
                    active_app,
                    stt_provider=stt_provider,
                )
            streaming_text = ""
            audio_duration_s = len(audio) / float(config.AUDIO_SAMPLE_RATE)
            if streaming_session is not None:
                if stt_provider == streaming_provider:
                    wait_s = self._streaming_finalize_timeout(streaming_session, perf_cfg, audio_duration_s)
                    streaming_text = self._finish_streaming_stt(streaming_session, wait_s=wait_s)
                    if streaming_text and not self._usable_streaming_text(streaming_text, audio_duration_s):
                        print(
                            f"STREAMING_STT_DISCARDED chars={len(streaming_text.strip())} duration={audio_duration_s:.2f}s",
                            flush=True,
                        )
                        streaming_text = ""
                else:
                    self._finish_streaming_stt(streaming_session, wait_s=0.05)
                streaming_session = None
            context_wait_s = 0.0 if streaming_text and not mode.llm_enabled else context_budget
            prefetched_contexts, vocab_hints = self._finish_context_prefetch(context_prefetch, wait_s=context_wait_s)
            contexts.update(prefetched_contexts)

            # Build context prompt for cloud/local
            prompt_parts: list[str] = []
            if vocab_hints:
                prompt_parts.append(f"Vocabulary hints:\n{vocab_hints}")
            if contexts.get("ui_automation", ""):
                prompt_parts.append(f"Focused control:\n{contexts['ui_automation']}")
            if contexts.get("clipboard", ""):
                prompt_parts.append(f"Recent clipboard:\n{contexts['clipboard']}")
            if contexts.get("selected_text", ""):
                prompt_parts.append(f"Selected text:\n{contexts['selected_text']}")
            prompt_text = "\n\n".join(prompt_parts) if prompt_parts else None

            def _local_transcribe() -> str:
                return transcribe(
                    audio,
                    context_words=vocab_hints,
                    selected_text=contexts.get("selected_text", ""),
                    clipboard_context=contexts.get("clipboard", ""),
                    ui_automation_text=contexts.get("ui_automation", ""),
                )

            raw_text = ""
            try:
                with timed("dictation_transcribe_total"):
                    if streaming_text:
                        raw_text = streaming_text
                        print("STREAMING_STT_USED", flush=True)
                    elif stt_provider == "local":
                        raw_text = _local_transcribe()
                    else:
                        # Cloud STT; fall back locally so a missing key or transient
                        # provider error does not drop the dictation.
                        self.signals.set_status.emit("Cloud transcribing...")
                        from core.transcriber import transcribe_cloud
                        from core.secrets import get_key
                        service = STT_KEY_SERVICES.get(stt_provider, stt_provider.replace("_whisper", ""))
                        key = get_key(service)
                        if not key and stt_provider == "groq_whisper":
                            key = get_key("groq")
                        compat_base_url = ""
                        if stt_provider == "openai_compatible_stt":
                            compat_base_url = settings.get("stt", {}).get("openai_compat_url", "")
                        elif stt_provider == "nvidia_nim_parakeet":
                            compat_base_url = settings.get("stt", {}).get("nvidia_nim_url", "")
                        if not key and stt_provider not in ("openai_compatible_stt", "nvidia_nim_parakeet"):
                            self.signals.set_status.emit("No cloud key. Using local model...")
                            stt_provider = "local"
                            stt_model = _stt_model_name(stt_provider)
                            raw_text = _local_transcribe()
                        else:
                            try:
                                with timed("dictation_cloud_request"):
                                    raw_text = transcribe_cloud(
                                        audio,
                                        stt_provider,
                                        key,
                                        language=mode.language or config.WHISPER_LANGUAGE,
                                        prompt=prompt_text,
                                        model=stt_model,
                                        base_url=compat_base_url,
                                    )
                            except Exception as cloud_exc:
                                print(f"Cloud STT failed for {stt_provider}: {cloud_exc}", flush=True)
                                self.signals.set_status.emit("Cloud failed. Using local model...")
                                stt_provider = "local"
                                stt_model = _stt_model_name(stt_provider)
                                raw_text = _local_transcribe()
            except Exception as exc:
                self.signals.set_processing.emit(False)
                self.signals.set_status.emit(f"Error: {exc}")
                time.sleep(2.0)
                self.signals.hide_overlay.emit()
                threading.Thread(
                    target=self._save_dictation_background,
                    args=(started_at, duration_ms, active_app, window_title, mode_id, "", ""),
                    kwargs={"contexts": contexts, "error": str(exc), "stt_provider": stt_provider, "stt_model": stt_model},
                    daemon=True,
                ).start()
                return

            if self._cancelled:
                self.signals.set_processing.emit(False)
                self.signals.hide_overlay.emit()
                return

            if not raw_text.strip():
                self.signals.set_status.emit("No speech detected.")
                time.sleep(0.05)
                self.signals.hide_overlay.emit()
                threading.Thread(
                    target=self._save_dictation_background,
                    args=(started_at, duration_ms, active_app, window_title, mode_id, "", ""),
                    kwargs={"contexts": contexts, "error": "No speech detected", "stt_provider": stt_provider, "stt_model": stt_model},
                    daemon=True,
                ).start()
                return

            with timed("format_and_replacements"):
                formatted = apply_replacements(format_transcription(raw_text, active_app, window_title, mode))

            llm_processed = 0
            if mode.llm_enabled and mode.llm_provider:
                self.signals.set_status.emit("LLM processing...")
                try:
                    from core.llm import process as llm_process
                    from core.secrets import get_key
                    base_url = ""
                    api_key = None
                    if mode.llm_provider == "ollama":
                        base_url = settings.get("llm", {}).get("ollama_url", "http://localhost:11434")
                    elif mode.llm_provider == "openai_compat":
                        base_url = settings.get("llm", {}).get("openai_compat_url", "http://localhost:8000")
                        api_key = get_key("openai_compat")
                    elif mode.llm_provider == "openai":
                        api_key = get_key("openai")
                    elif mode.llm_provider == "anthropic":
                        api_key = get_key("anthropic")
                    elif mode.llm_provider == "groq":
                        api_key = get_key("groq")
                    llm_result = llm_process(
                        formatted,
                        prompt_template=mode.llm_prompt,
                        provider_name=mode.llm_provider,
                        model=mode.llm_model or "llama3.1",
                        timeout_s=10,
                        base_url=base_url,
                        api_key=api_key,
                    )
                    if llm_result and llm_result != formatted:
                        formatted = llm_result
                        llm_processed = 1
                        self.signals.set_status.emit(f"Pasting: {formatted[:50]}...")
                except Exception as exc:
                    self.signals.set_status.emit(f"LLM error: {exc}")
                    time.sleep(1.5)

            if self._cancelled:
                self.signals.set_processing.emit(False)
                self.signals.hide_overlay.emit()
                return

            self.signals.set_status.emit(f"Pasting: {formatted[:50]}...")

            new_words = set()
            for word in extract_useful_terms(formatted, limit=80, source="transcription", include_phrases=False):
                clean = word.lower()
                if clean and clean not in self._recent_words:
                    new_words.add(clean)
                    self._recent_words.add(clean)
            if new_words:
                # Add to dictionary in a background thread to not delay pasting
                threading.Thread(target=add_words_from_list, args=(list(new_words),), kwargs={"source": "transcription"}, daemon=True).start()

            # Determine paste method
            paste_method, restore_clipboard, auto_send, paste_delay, paste_fast_path = self._resolve_paste_method(active_app, mode)
            paste_succeeded = 0
            try:
                preceding_text = ""
                preceding_text_known = sys.platform != "darwin"
                if sys.platform == "darwin":
                    try:
                        from core.native import (
                            accessibility_access_granted,
                            focused_control_snapshot,
                            preceding_text_from_snapshot,
                        )

                        if accessibility_access_granted():
                            with timed("insertion_spacing_context"):
                                focus_snapshot = focused_control_snapshot(active_app, timeout=0.22)
                            preceding_text, preceding_text_known, cursor_at_start = preceding_text_from_snapshot(
                                focus_snapshot
                            )
                            # Some apps expose a focused element but not its text. Treat that
                            # as unknown unless Accessibility explicitly reports cursor 0.
                            if cursor_at_start:
                                preceding_text_known = True
                    except Exception:
                        preceding_text = ""
                        preceding_text_known = False
                same_target_as_last_paste = self._last_paste_target == (active_app or "", window_title or "")
                formatted = prepare_text_for_insertion(
                    formatted,
                    preceding_text=preceding_text,
                    preceding_text_known=preceding_text_known,
                    fallback_needs_leading_space=bool(
                        same_target_as_last_paste and self._last_paste_needs_space
                    ),
                )
                print(
                    "INSERTION_SPACING "
                    f"known={int(preceding_text_known)} "
                    f"preceding_tail={preceding_text[-12:]!r} "
                    f"fallback={int(same_target_as_last_paste and self._last_paste_needs_space)} "
                    f"leading_space={int(formatted.startswith(' '))}",
                    flush=True,
                )
                with timed("paste_delivery"):
                    print(
                        f"PASTE_ATTEMPT app={active_app!r} method={paste_method!r} chars={len(formatted)}",
                        flush=True,
                    )
                    paste_succeeded = 1 if paste_text(
                        formatted,
                        method=paste_method,
                        restore_clipboard=restore_clipboard,
                        auto_send=auto_send,
                        active_app=active_app,
                        paste_delay_ms=paste_delay,
                        fast_path=paste_fast_path,
                    ) else 0
                if not paste_succeeded:
                    raise RuntimeError("Paste command was not delivered.")
                print("PASTE_DELIVERED", flush=True)
                self.signals.set_processing.emit(False)
                self.signals.hide_overlay.emit()
            except Exception as paste_exc:
                print(f"PASTE_FAILED {paste_exc}", flush=True)
                self.signals.set_processing.emit(False)
                self.signals.set_status.emit(f"Paste failed: {paste_exc}")
                time.sleep(1.5)
                self.signals.hide_overlay.emit()

            self._last_dictation_text = formatted
            if paste_succeeded:
                self._last_paste_target = (active_app or "", window_title or "")
                self._last_paste_needs_space = bool((formatted.rstrip()[-1:] if formatted.rstrip() else "") in ".!?:;")
            mark_clipboard_pasted()

            # Determine if we should retain audio
            audio_path = None
            if settings.get("privacy", {}).get("store_audio_history", False):
                try:
                    from core.paths import get_app_data_dir
                    import wave
                    audio_dir = os.path.join(get_app_data_dir(), "audio")
                    os.makedirs(audio_dir, exist_ok=True)
                    ts = time.strftime("%Y%m%d_%H%M%S")
                    audio_path = os.path.join(audio_dir, f"dictation_{ts}.wav")
                    with wave.open(audio_path, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(config.AUDIO_SAMPLE_RATE)
                        wf.writeframes((audio * 32767).astype(np.int16).tobytes())
                except Exception:
                    audio_path = None

            threading.Thread(
                target=self._save_dictation_background,
                args=(started_at, duration_ms, active_app, window_title, mode_id, raw_text, formatted),
                kwargs={
                    "contexts": contexts,
                    "audio_path": audio_path,
                    "stt_provider": stt_provider,
                    "stt_model": stt_model,
                    "llm_processed": llm_processed,
                    "paste_method": paste_method,
                    "paste_succeeded": paste_succeeded,
                },
                daemon=True,
            ).start()
        finally:
            if streaming_session is not None:
                try:
                    self._finish_streaming_stt(streaming_session, wait_s=0.05)
                except Exception:
                    pass
            self._restore_audio_ducking()
            self._processing_job_active.clear()
            self._clear_longform_lock()
            if acquired_lock:
                self._session_lock.release()

    def _on_hotkey_pressed(self):
        """Called when user presses the dictation hotkey. Start dictation session in a background thread."""
        self._dictation_press_started_at = time.monotonic()
        if not self._model_ready.is_set():
            self._store_pending_active_target()
            self._mark_pre_ready_hotkey()
            self._show_model_loading_overlay()
            return
        if self._pre_ready_hotkey_still_held():
            return
        if not self._session_lock.acquire(blocking=False):
            if self.recorder.is_recording:
                self._request_longform_lock()
            return
        self._clear_longform_lock()
        active_app, window_title = self._capture_active_target(include_title=False)
        if active_app or window_title:
            print(f"DICTATION_TARGET_CAPTURED app={active_app!r} title={window_title!r}", flush=True)
        self._prime_listening_overlay()
        if self._is_alt_pressed():
            self._request_longform_lock()
        threading.Thread(
            target=lambda: self._run_one_dictation_session(
                lock_acquired=True,
                overlay_primed=True,
                initial_active_app=active_app,
                initial_window_title=window_title,
            ),
            daemon=True,
        ).start()

    def _on_toggle_pressed(self):
        """Called when user presses the toggle recording hotkey."""
        if not self._model_ready.is_set():
            self._store_pending_active_target()
            self._mark_pre_ready_hotkey()
            self._show_model_loading_overlay()
            return
        if self._pre_ready_hotkey_still_held():
            return
        if self.recorder.is_recording:
            # If already recording, this acts like releasing the hotkey
            self._cancelled = False
            return
        if not self._session_lock.acquire(blocking=False):
            return
        self._clear_longform_lock()
        active_app, window_title = self._capture_active_target(include_title=False)
        if active_app or window_title:
            print(f"DICTATION_TARGET_CAPTURED app={active_app!r} title={window_title!r}", flush=True)
        self._prime_listening_overlay()
        if self._is_alt_pressed():
            self._request_longform_lock()
        threading.Thread(
            target=lambda: self._run_one_dictation_session(
                toggle_mode=True,
                lock_acquired=True,
                overlay_primed=True,
                initial_active_app=active_app,
                initial_window_title=window_title,
            ),
            daemon=True,
        ).start()

    def _on_cancel_pressed(self):
        """Called when user presses the cancel hotkey during recording."""
        if self.recorder.is_recording:
            self._cancelled = True

    def _on_mode_next(self):
        """Cycle to the next enabled mode."""
        self._refresh_modes_list()
        if not self._modes_list:
            return
        self._current_mode_index = (self._current_mode_index + 1) % len(self._modes_list)
        mode = self._modes_list[self._current_mode_index]
        self.signals.mode_changed.emit(mode.name)

    def _on_mode_prev(self):
        """Cycle to the previous enabled mode."""
        self._refresh_modes_list()
        if not self._modes_list:
            return
        self._current_mode_index = (self._current_mode_index - 1) % len(self._modes_list)
        mode = self._modes_list[self._current_mode_index]
        self.signals.mode_changed.emit(mode.name)

    def _on_repeat_last(self):
        """Paste the last dictation text again."""
        if not self._last_dictation_text:
            return
        active_app = get_active_window_name()
        settings = load_settings()
        paste_cfg = settings.get("paste", {})
        perf_cfg = settings.get("performance", {})
        method = paste_cfg.get("method", "clipboard_paste")
        restore = paste_cfg.get("restore_clipboard", False)
        auto_send = paste_cfg.get("auto_send_enter", False)
        paste_delay = int(perf_cfg.get("paste_delay_ms", 30))
        fast_path = bool(perf_cfg.get("paste_fast_path_enabled", True))
        # Check per-app override
        overrides = paste_cfg.get("per_app_overrides", {})
        active_lower = active_app.lower()
        for app_substring, override in overrides.items():
            if app_substring.lower() in active_lower:
                if "method" in override:
                    method = override["method"]
                if "restore_clipboard" in override:
                    restore = override["restore_clipboard"]
                if "auto_send_enter" in override:
                    auto_send = override["auto_send_enter"]
                if "fast_path" in override:
                    fast_path = bool(override["fast_path"])
                break
        for app_substring, delay in perf_cfg.get("paste_delay_overrides", {}).items():
            if app_substring.lower() in active_lower:
                try:
                    paste_delay = int(delay)
                except (TypeError, ValueError):
                    pass
                break
        if fast_path:
            fast_all_apps = bool(perf_cfg.get("paste_fast_all_apps", True))
            if not fast_all_apps:
                fast_apps = perf_cfg.get("paste_fast_apps", [])
                if not isinstance(fast_apps, list):
                    fast_apps = []
                fast_path = any(str(app).strip().lower() in active_lower for app in fast_apps if str(app).strip())
            if method != "clipboard_paste" or restore:
                fast_path = False
            if fast_path:
                try:
                    paste_delay = min(paste_delay, max(0, int(perf_cfg.get("paste_fast_delay_ms", 12))))
                except (TypeError, ValueError):
                    paste_delay = min(paste_delay, 12)
        try:
            paste_text(
                self._last_dictation_text,
                method=method,
                restore_clipboard=restore,
                auto_send=auto_send,
                active_app=active_app,
                paste_delay_ms=paste_delay,
                fast_path=fast_path,
            )
        except Exception:
            pass

    def _on_open_history(self):
        """Signal to open the history window/tab."""
        self.signals.open_history.emit()

    def _unregister_shortcuts(self):
        """Remove all registered keyboard hotkeys."""
        for hk in self._registered_hotkeys:
            try:
                hotkeys.remove_hotkey(hk)
            except Exception:
                pass
        self._registered_hotkeys.clear()

    def _register_shortcuts(self):
        """Register all configured keyboard shortcuts."""
        self._unregister_shortcuts()
        if self._hotkeys_paused:
            print("Hotkeys paused for shortcut capture.", flush=True)
            return
        settings = load_settings()
        shortcuts = settings.get("shortcuts", {})

        def _add(hotkey_str: str | None, callback):
            hotkey_str = _normalize_keyboard_hotkey(hotkey_str)
            if not hotkey_str:
                return
            try:
                hk = hotkeys.add_hotkey(hotkey_str, callback, suppress=False)
                self._registered_hotkeys.append(hk)
                print(f"Registered hotkey: {hotkey_str}", flush=True)
            except Exception as exc:
                print(f"Could not register hotkey '{hotkey_str}': {exc}", flush=True)

        dictation_hk = shortcuts.get("dictation") or config.DICTATION_HOTKEY
        _add(dictation_hk, self._on_hotkey_pressed)
        _add("alt", self._on_alt_lock_pressed)
        _add(shortcuts.get("toggle_recording"), self._on_toggle_pressed)
        _add(shortcuts.get("cancel"), self._on_cancel_pressed)
        _add(shortcuts.get("mode_next"), self._on_mode_next)
        _add(shortcuts.get("mode_prev"), self._on_mode_prev)
        _add(shortcuts.get("repeat_last"), self._on_repeat_last)
        _add(shortcuts.get("open_history"), self._on_open_history)

    def _set_hotkeys_paused(self, paused: bool):
        paused = bool(paused)
        if self._hotkeys_paused == paused:
            return
        self._hotkeys_paused = paused
        self._clear_pre_ready_hotkey_suppression()
        if paused:
            self._unregister_shortcuts()
            print("Hotkeys paused for shortcut capture.", flush=True)
        else:
            self._register_shortcuts()

    def _load_engine_background(self):
        record_timing("engine_import_phase", (time.perf_counter() - _PROCESS_START) * 1000.0)
        model_name = config.WHISPER_MODEL_SIZE
        engine_name = "NVIDIA Parakeet" if model_name.lower().startswith("nvidia/parakeet") else "Whisper"
        target = "GPU" if config.WHISPER_DEVICE == "cuda" else config.WHISPER_DEVICE.upper()
        print(f"Preparing dictation engine with {engine_name} local fallback on {target}...", flush=True)

        try:
            settings = load_settings()
            try:
                self.recorder.refresh_settings(settings)
                with timed("recorder_prepare"):
                    self.recorder.prepare()
            except Exception as exc:
                print(f"Mic warmup skipped: {exc}", flush=True)

            self._model_ready.set()
            self.signals.set_model_loading.emit(False)
            _write_engine_ready_file(model_name)
            print("ENGINE_READY", flush=True)
            self._start_stdin_command_reader()
            self._ensure_live_recognizer()
            if not self._start_pre_ready_hotkey_dictation_if_held():
                threading.Thread(target=self._clear_pre_ready_hotkey_after_release, daemon=True).start()
            shortcuts = settings.get("shortcuts", {})
            dictation_hk = _normalize_keyboard_hotkey(shortcuts.get("dictation") or config.DICTATION_HOTKEY) or config.DICTATION_HOTKEY
            print(f"Quick dictation:  {dictation_hk.replace('+', ' + ').title()} (hold)", flush=True)
            print(f"Long-form mode:   {dictation_hk.replace('+', ' + ').title()} + Alt (then let go)", flush=True)
            if shortcuts.get("toggle_recording"):
                print(f"Toggle recording: {shortcuts['toggle_recording'].replace('+', ' + ').title()}", flush=True)
            if shortcuts.get("cancel"):
                print(f"Cancel:           {shortcuts['cancel'].replace('+', ' + ').title()}", flush=True)
            print("Press Ctrl+C in this terminal to quit.\n", flush=True)
            threading.Timer(1.2, self._hide_loading_overlay_if_idle).start()
            threading.Thread(target=lambda: self._prewarm_cloud_stt_clients(settings), daemon=True).start()
            threading.Thread(target=lambda: self._warm_local_model_when_idle(model_name, settings), daemon=True).start()
        except Exception as exc:
            self._model_failed = str(exc)
            self.signals.set_model_loading.emit(False)
            traceback.print_exc()
            os._exit(1)

    def _start_stdin_command_reader(self):
        if self._stdin_command_reader_started:
            return
        self._stdin_command_reader_started = True

        def _reader():
            try:
                for line in sys.stdin:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if payload.get("command") == "transcribe_last_dictation":
                        request_id = str(payload.get("requestId") or "")
                        threading.Thread(
                            target=lambda: self._transcribe_last_dictation_command(request_id),
                            daemon=True,
                        ).start()
                    elif payload.get("command") == "benchmark_stt":
                        request_id = str(payload.get("requestId") or "")
                        threading.Thread(
                            target=lambda: self._benchmark_stt_command(request_id),
                            daemon=True,
                        ).start()
                    elif payload.get("command") == "set_hotkeys_paused":
                        self._set_hotkeys_paused(bool(payload.get("paused")))
            except Exception:
                pass

        threading.Thread(target=_reader, daemon=True).start()

    def _emit_backup_transcription_result(self, request_id: str, ok: bool, text: str = "", error: str = ""):
        payload = {
            "requestId": request_id,
            "ok": bool(ok),
            "text": text or "",
            "error": error or "",
        }
        print("BACKUP_TRANSCRIPTION_RESULT " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)

    def _emit_stt_benchmark_result(
        self,
        request_id: str,
        ok: bool,
        results: list[dict] | None = None,
        error: str = "",
        sample_seconds: float = 0.0,
    ):
        payload = {
            "requestId": request_id,
            "ok": bool(ok),
            "results": results or [],
            "error": error or "",
            "sampleSeconds": round(float(sample_seconds or 0.0), 2),
        }
        print("STT_BENCHMARK_RESULT " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)

    def _benchmark_audio_sample(self, audio: np.ndarray) -> np.ndarray:
        audio = np.asarray(audio, dtype=np.float32).flatten()
        max_samples = int(config.AUDIO_SAMPLE_RATE * 4)
        min_samples = int(config.AUDIO_SAMPLE_RATE * 0.5)
        if len(audio) > max_samples:
            audio = audio[:max_samples]
        if len(audio) < min_samples:
            return np.zeros(0, dtype=np.float32)
        return audio.astype(np.float32, copy=False)

    def _benchmark_stt_command(self, request_id: str):
        try:
            from core.dictation_backup import finalize_last_dictation_wav, load_last_dictation_audio
            from core.secrets import get_key
            from core.transcriber import (
                DEFAULT_DEEPGRAM_STT_MODEL,
                DEFAULT_GROQ_STT_MODEL,
                DEFAULT_NVIDIA_NIM_STT_MODEL,
                DEFAULT_OPENAI_STT_MODEL,
                NVIDIA_RIVA_STREAMING_MODEL,
                trim_silence,
                transcribe_cloud,
            )

            finalize_last_dictation_wav()
            audio = self._benchmark_audio_sample(trim_silence(load_last_dictation_audio()))
            sample_seconds = len(audio) / float(config.AUDIO_SAMPLE_RATE) if len(audio) else 0.0
            if len(audio) == 0:
                self._emit_stt_benchmark_result(
                    request_id,
                    False,
                    error="Record one normal dictation first so the benchmark has a shared audio sample.",
                )
                return

            settings = load_settings()
            language = config.WHISPER_LANGUAGE
            results: list[dict] = []

            def run_case(label: str, provider: str, key_name: str, model: str, base_url: str = ""):
                try:
                    api_key = get_key(key_name)
                except Exception:
                    api_key = None
                if not api_key and provider not in {"nvidia_nim_parakeet", "openai_compatible_stt"}:
                    return
                started = time.perf_counter()
                try:
                    text = transcribe_cloud(
                        audio,
                        provider,
                        api_key,
                        language=language,
                        model=model,
                        base_url=base_url,
                    )
                    results.append(
                        {
                            "label": label,
                            "provider": provider,
                            "model": model,
                            "ok": True,
                            "ms": round((time.perf_counter() - started) * 1000.0, 1),
                            "chars": len((text or "").strip()),
                            "preview": (text or "").strip()[:120],
                        }
                    )
                except Exception as exc:
                    results.append(
                        {
                            "label": label,
                            "provider": provider,
                            "model": model,
                            "ok": False,
                            "ms": round((time.perf_counter() - started) * 1000.0, 1),
                            "error": str(exc)[:180],
                        }
                    )

            try:
                nvidia_key = get_key("nvidia")
            except Exception:
                nvidia_key = None
            nvidia_base_url = settings.get("stt", {}).get("nvidia_nim_url", "")
            if nvidia_key:
                started = time.perf_counter()
                try:
                    session = NvidiaStreamingTranscriber(
                        nvidia_key,
                        base_url=nvidia_base_url,
                        language=language,
                        model=NVIDIA_RIVA_STREAMING_MODEL,
                    )
                    session.start()
                    chunk_samples = max(160, int(config.AUDIO_SAMPLE_RATE * 0.032))
                    for start in range(0, len(audio), chunk_samples):
                        session.feed_audio(audio[start : start + chunk_samples])
                    text = session.finish(timeout_s=0.9)
                    error = session.error
                    if error:
                        raise RuntimeError(error)
                    results.append(
                        {
                            "label": "NVIDIA Parakeet RNNT streaming",
                            "provider": "nvidia_nim_parakeet",
                            "model": NVIDIA_RIVA_STREAMING_MODEL,
                            "ok": True,
                            "ms": round((time.perf_counter() - started) * 1000.0, 1),
                            "chars": len((text or "").strip()),
                            "preview": (text or "").strip()[:120],
                        }
                    )
                except Exception as exc:
                    results.append(
                        {
                            "label": "NVIDIA Parakeet RNNT streaming",
                            "provider": "nvidia_nim_parakeet",
                            "model": NVIDIA_RIVA_STREAMING_MODEL,
                            "ok": False,
                            "ms": round((time.perf_counter() - started) * 1000.0, 1),
                            "error": str(exc)[:180],
                        }
                    )
                run_case("NVIDIA Parakeet TDT 0.6B final", "nvidia_nim_parakeet", "nvidia", DEFAULT_NVIDIA_NIM_STT_MODEL, nvidia_base_url)
                run_case("NVIDIA Parakeet CTC 0.6B final", "nvidia_nim_parakeet", "nvidia", "parakeet-ctc-0.6b-asr", nvidia_base_url)
            run_case("Groq Whisper v3 Turbo", "groq_whisper", "groq", DEFAULT_GROQ_STT_MODEL)
            run_case("OpenAI speech", "openai_whisper", "openai", DEFAULT_OPENAI_STT_MODEL)
            run_case("Deepgram Nova", "deepgram", "deepgram", DEFAULT_DEEPGRAM_STT_MODEL)

            if not results:
                self._emit_stt_benchmark_result(
                    request_id,
                    False,
                    error="No benchmarkable cloud STT API keys are saved.",
                    sample_seconds=sample_seconds,
                )
                return
            results.sort(key=lambda item: (not bool(item.get("ok")), float(item.get("ms") or 999999)))
            self._emit_stt_benchmark_result(request_id, True, results=results, sample_seconds=sample_seconds)
        except Exception as exc:
            self._emit_stt_benchmark_result(request_id, False, error=str(exc))

    def _transcribe_last_dictation_command(self, request_id: str):
        if not self._model_ready.wait(timeout=180):
            error = self._model_failed or "The dictation engine is still loading."
            self._emit_backup_transcription_result(request_id, False, error=error)
            return
        if not self._session_lock.acquire(blocking=False):
            self._emit_backup_transcription_result(
                request_id,
                False,
                error="Finish the current dictation before transcribing the backup.",
            )
            return
        try:
            from core.dictation_backup import finalize_last_dictation_wav, load_last_dictation_audio

            finalize_last_dictation_wav()
            audio = load_last_dictation_audio()
            raw_text = transcribe(audio, context_words=get_prompt_words(80))
            final_text = apply_replacements(
                format_transcription(
                    raw_text,
                    active_app="last-dictation",
                    window_title="Last dictation backup",
                )
            )
            self._emit_backup_transcription_result(request_id, True, text=(final_text or raw_text).strip())
        except Exception as exc:
            self._emit_backup_transcription_result(request_id, False, error=str(exc))
        finally:
            try:
                self._session_lock.release()
            except RuntimeError:
                pass

    def run(self):
        self._start_stdin_command_reader()
        if sys.platform == "darwin":
            try:
                from core.native import request_accessibility_access

                if not request_accessibility_access(prompt=True):
                    print("ACCESSIBILITY_PERMISSION_MISSING paste_and_hotkeys_may_be_blocked", flush=True)
            except Exception:
                pass
        self._register_shortcuts()
        threading.Thread(target=self._load_engine_background, daemon=True).start()
        sys.exit(self.app.exec())


if __name__ == "__main__":
    # Handle --file=path for headless file transcription (Open with integration)
    file_arg = None
    model_arg = None
    for arg in sys.argv[1:]:
        if arg.startswith("--file="):
            file_arg = arg.split("=", 1)[1]
        elif arg.startswith("--model="):
            model_arg = arg.split("=", 1)[1]
    if model_arg:
        config.WHISPER_MODEL_SIZE = model_arg
    if file_arg:
        print(f"Transcribing file: {file_arg}", flush=True)
        try:
            result = transcribe_file(file_arg)
            print(result["final_text"], flush=True)
        except Exception as exc:
            print(f"Error: {exc}", flush=True)
            sys.exit(1)
    else:
        WhisperApp().run()
