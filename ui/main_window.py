"""Main Whisperer window.

The dashboard is rendered by the React app in ``whisperer-app/dist`` while this
module keeps ownership of the real native/Python behavior: tray integration,
engine subprocess lifecycle, settings persistence, GPU selection, and device
discovery. The dictation overlay remains in the engine process.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import ctypes
import tempfile
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any

from PyQt6.QtCore import Qt, QObject, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QIcon, QKeySequence, QShortcut
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget

import config
from core import hotkeys
from core.paths import get_app_data_dir
from core.settings import load_settings, save_settings
from ui.app_icon import APP_USER_MODEL_ID, app_icon_path
from ui.overlay import WaveformOverlay
from ui.tray import TrayIcon


ENGINE_FORCE_STOP_RESTART_CODE = 42
GPU_AUTO_VALUE = "auto"
GROQ_RUNTIME_VALUE = "groq_api"
NVIDIA_NIM_RUNTIME_VALUE = "nvidia_nim_api"
NVIDIA_NIM_DEFAULT_MODEL = "parakeet-tdt-0.6b-v2"
LOADING_PREVIEW_HIDE_MS = 1400
LOADING_READY_MORPH_HIDE_MS = 900
LOADING_INTERACTION_HIDE_RETRY_MS = 450
LOADING_HOTKEY_RELEASE_RETRY_MS = 120
LOADING_RELEASE_POLL_MS = 35
NOISY_ENGINE_LINE_PARTS = (
    "OneLogger:",
    "No exporters were provided.",
    "error_handling_strategy",
    "no telemetry data will be collected",
)

QUIET_MODEL_ENV = {
    "NEMO_LOGGING_LEVEL": "ERROR",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "TOKENIZERS_PARALLELISM": "false",
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8:replace",
}

API_KEY_SERVICES = {
    "openai": "OpenAI",
    "groq": "Groq",
    "deepgram": "Deepgram",
    "nvidia": "NVIDIA",
    "anthropic": "Anthropic",
    "openai_compat": "OpenAI-compatible LLM",
    "openai_compat_stt": "OpenAI-compatible STT",
}

UPDATE_REPO = "kynewman/Whisperer-Mac"
UPDATE_RELEASES_URL = f"https://github.com/{UPDATE_REPO}/releases"
UPDATE_LATEST_API_URL = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
UPDATE_ALL_RELEASES_API_URL = f"https://api.github.com/repos/{UPDATE_REPO}/releases"


class _DwmMargins(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]

MODEL_OPTIONS = [
    {
        "value": "deepdml/faster-whisper-large-v3-turbo-ct2",
        "label": "Whisper v3 Turbo",
        "size": "1.6 B",
        "badge": "Local",
        "speed": "Balanced",
    },
    {
        "value": "large-v3",
        "label": "Whisper Large v3",
        "size": "1.55 B",
        "badge": "Local",
        "speed": "Accurate",
    },
    {
        "value": "nvidia/parakeet-unified-en-0.6b",
        "label": "NVIDIA Parakeet Unified 0.6B",
        "size": "0.6 B",
        "badge": "Local",
        "speed": "Fastest",
    },
]

GROQ_STT_MODEL_META = {
    "whisper-large-v3-turbo": {
        "label": "Whisper Large v3 Turbo",
        "price": "$0.04/hr",
        "hint": "Fastest",
    },
    "whisper-large-v3": {
        "label": "Whisper Large v3",
        "price": "$0.111/hr",
        "hint": "Most accurate",
    },
}

NVIDIA_NIM_MODEL_OPTIONS = [
    {
        "value": "parakeet-tdt-0.6b-v2",
        "label": "NVIDIA Parakeet TDT 0.6B v2",
        "hint": "Fast, English",
    },
    {
        "value": "parakeet-ctc-0.6b-asr",
        "label": "NVIDIA Parakeet CTC 0.6B",
        "hint": "English",
    },
    {
        "value": "parakeet-1.1b-rnnt-multilingual-asr",
        "label": "NVIDIA Parakeet RNNT 1.1B",
        "hint": "Multilingual",
    },
]


def _nvidia_nim_model_value(value: str | None) -> str:
    aliases = {
        "parakeet-1.1b-rnnt-multilingual": "parakeet-1.1b-rnnt-multilingual-asr",
    }
    cleaned = (value or "").strip()
    return aliases.get(cleaned, cleaned)


def _available_model_options() -> list[dict[str, str]]:
    if sys.platform == "darwin":
        return [item for item in MODEL_OPTIONS if not item["value"].lower().startswith("nvidia/")]
    return MODEL_OPTIONS


def _react_index_url() -> QUrl:
    """Return the file URL for the built React entrypoint."""
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(here)
    candidates: list[str] = []
    if getattr(sys, "frozen", False):
        candidates.extend(
            [
                os.path.join(getattr(sys, "_MEIPASS", ""), "whisperer-app", "dist", "index.html"),
                os.path.join(os.path.dirname(sys.executable), "whisperer-app", "dist", "index.html"),
                os.path.join(os.path.dirname(sys.executable), "_internal", "whisperer-app", "dist", "index.html"),
            ]
        )
    candidates.append(os.path.join(project_root, "whisperer-app", "dist", "index.html"))
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return QUrl.fromLocalFile(candidate)
    return QUrl.fromLocalFile(candidates[-1])


def _normalize_keyboard_hotkey(hotkey: str | None) -> str | None:
    """Convert UI key names into names understood by the native hotkey backend."""
    return hotkeys.normalize_hotkey(hotkey)


def _version_parts(value: str | None) -> tuple[int, ...]:
    parts = [int(part) for part in re.findall(r"\d+", value or "")[:4]]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _release_version(tag: str | None, name: str | None = None) -> str:
    source = tag or name or ""
    match = re.search(r"(\d+(?:\.\d+){1,3})", source)
    return match.group(1) if match else source.strip().lstrip("v")


def _is_newer_release(current: str, latest: str) -> bool:
    return _version_parts(latest) > _version_parts(current)


def _utc_timestamp() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _select_macos_release_asset(release: dict[str, Any]) -> dict[str, Any] | None:
    assets = [asset for asset in release.get("assets", []) if isinstance(asset, dict)]
    dmg_assets = [
        asset
        for asset in assets
        if str(asset.get("name") or "").lower().endswith(".dmg")
        and str(asset.get("browser_download_url") or "").strip()
    ]
    preferred = [
        asset
        for asset in dmg_assets
        if any(token in str(asset.get("name") or "").lower() for token in ("mac", "macos", "arm64", "whisperer"))
    ]
    if preferred:
        return preferred[0]
    return dmg_assets[0] if dmg_assets else None


def _app_bundle_path() -> str:
    path = os.path.abspath(sys.executable)
    while path and path != os.path.dirname(path):
        if path.endswith(".app"):
            return path
        path = os.path.dirname(path)
    default_path = "/Applications/Whisperer.app"
    return default_path if os.path.isdir(default_path) else ""


class Bridge(QObject):
    """Object exposed to JavaScript through QWebChannel."""

    engineStateChanged = pyqtSignal(str)
    settingsChanged = pyqtSignal(str)

    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self._window = window

    @pyqtSlot()
    def minimize(self):
        self._window.showMinimized()

    @pyqtSlot()
    def maximize(self):
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    @pyqtSlot()
    def close(self):
        self._window.close()

    @pyqtSlot()
    def showWindow(self):
        self._window.show_window()

    @pyqtSlot()
    def startDrag(self):
        if self._window.isMaximized():
            return
        handle = self._window.windowHandle()
        if handle:
            handle.startSystemMove()

    @pyqtSlot()
    def startResize(self):
        if self._window.isMaximized():
            return
        handle = self._window.windowHandle()
        if handle:
            handle.startSystemResize(Qt.Edge.RightEdge | Qt.Edge.BottomEdge)

    @pyqtSlot()
    def startEngine(self):
        self._window.start_engine()

    @pyqtSlot()
    def stopEngine(self):
        self._window.stop_engine()

    @pyqtSlot(result=str)
    def engineState(self) -> str:
        return self._window.engine_state()

    @pyqtSlot(result=str)
    def appSnapshot(self) -> str:
        return self._window.snapshot_json()

    @pyqtSlot(result=str)
    def vocabularySnapshot(self) -> str:
        return self._window.vocabulary_snapshot_json()

    @pyqtSlot(result=str)
    def historySnapshot(self) -> str:
        return self._window.history_snapshot_json()

    @pyqtSlot(result=str)
    def modesSnapshot(self) -> str:
        return self._window.modes_snapshot_json()

    @pyqtSlot(result=str)
    def micLevel(self) -> str:
        return self._window.mic_level_json()

    @pyqtSlot(str, result=str)
    def setModel(self, value: str) -> str:
        return self._window.set_model(value)

    @pyqtSlot(str, result=str)
    def setGpu(self, value: str) -> str:
        return self._window.set_gpu(value)

    @pyqtSlot(str, result=str)
    def setMicrophone(self, value: str) -> str:
        return self._window.set_microphone(value)

    @pyqtSlot(str, result=str)
    def setInputChannel(self, value: str) -> str:
        return self._window.set_input_channel(value)

    @pyqtSlot(str, result=str)
    def addVocabularyWord(self, word: str) -> str:
        return self._window.add_vocabulary_word(word)

    @pyqtSlot(str, str, result=str)
    def addReplacementRule(self, match_text: str, replace_with: str) -> str:
        return self._window.add_replacement_rule(match_text, replace_with)

    @pyqtSlot(str, result=str)
    def copyText(self, text: str) -> str:
        QApplication.clipboard().setText(text or "")
        return self._window.history_snapshot_json()

    @pyqtSlot(result=str)
    def transcribeLastDictation(self) -> str:
        return self._window.transcribe_last_dictation()

    @pyqtSlot(int, result=str)
    def deleteDictation(self, dictation_id: int) -> str:
        return self._window.delete_dictation(dictation_id)

    @pyqtSlot(result=str)
    def purgeHistory(self) -> str:
        return self._window.purge_history()

    @pyqtSlot(str, result=str)
    def addMode(self, name: str) -> str:
        return self._window.add_mode(name)

    @pyqtSlot(int, result=str)
    def deleteMode(self, mode_id: int) -> str:
        return self._window.delete_mode(mode_id)

    @pyqtSlot(int, str, result=str)
    def updateMode(self, mode_id: int, patch_json: str) -> str:
        return self._window.update_mode(mode_id, patch_json)

    @pyqtSlot(int, str, str, int, result=str)
    def addAutoRule(self, mode_id: int, match_type: str, match_value: str, priority: int) -> str:
        return self._window.add_auto_rule(mode_id, match_type, match_value, priority)

    @pyqtSlot(int, result=str)
    def deleteAutoRule(self, rule_id: int) -> str:
        return self._window.delete_auto_rule(rule_id)

    @pyqtSlot(str, str, result=str)
    def setShortcut(self, name: str, value: str) -> str:
        return self._window.set_shortcut(name, value)

    @pyqtSlot(bool, result=str)
    def setShortcutCaptureActive(self, active: bool) -> str:
        return self._window.set_shortcut_capture_active(bool(active))

    @pyqtSlot(result=str)
    def shortcutModifierState(self) -> str:
        return self._window.shortcut_modifier_state()

    @pyqtSlot(str, str, str, result=str)
    def setSetting(self, section: str, key: str, value_json: str) -> str:
        try:
            value = json.loads(value_json)
        except json.JSONDecodeError:
            value = value_json
        return self._window.set_setting(section, key, value)

    @pyqtSlot(str, str, result=str)
    def setApiKey(self, service: str, value: str) -> str:
        return self._window.set_api_key(service, value)

    @pyqtSlot(str, result=str)
    def deleteApiKey(self, service: str) -> str:
        return self._window.delete_api_key(service)

    @pyqtSlot(str, result=str)
    def testApiKey(self, service: str) -> str:
        return self._window.test_api_key(service)

    @pyqtSlot(result=str)
    def checkForUpdates(self) -> str:
        return self._window.check_for_updates()

    @pyqtSlot(result=str)
    def installUpdate(self) -> str:
        return self._window.install_update()


_BRIDGE_SHIM = r"""
(function() {
  function install() {
    new QWebChannel(qt.webChannelTransport, function(channel) {
      var b = channel.objects.bridge;
      function callResult(name) {
        var args = Array.prototype.slice.call(arguments, 1);
        return new Promise(function(resolve) {
          args.push(function(result) { resolve(result); });
          b[name].apply(b, args);
        });
      }
      window.bridge = b;
      window.whisperer = {
        minimize: function() { b.minimize(); },
        maximize: function() { b.maximize(); },
        close: function() { b.close(); },
        showWindow: function() { b.showWindow(); },
        startDrag: function() { b.startDrag(); },
        startResize: function() { b.startResize(); },
        startEngine: function() { b.startEngine(); },
        stopEngine: function() { b.stopEngine(); },
        engineState: function() { return callResult("engineState"); },
        appSnapshot: function() { return callResult("appSnapshot"); },
        vocabularySnapshot: function() { return callResult("vocabularySnapshot"); },
        historySnapshot: function() { return callResult("historySnapshot"); },
        modesSnapshot: function() { return callResult("modesSnapshot"); },
        micLevel: function() { return callResult("micLevel"); },
        setModel: function(value) { return callResult("setModel", value); },
        setGpu: function(value) { return callResult("setGpu", value); },
        setMicrophone: function(value) { return callResult("setMicrophone", value); },
        setInputChannel: function(value) { return callResult("setInputChannel", value); },
        addVocabularyWord: function(word) { return callResult("addVocabularyWord", word); },
        addReplacementRule: function(matchText, replaceWith) { return callResult("addReplacementRule", matchText, replaceWith); },
        copyText: function(text) { return callResult("copyText", text); },
        transcribeLastDictation: function() { return callResult("transcribeLastDictation"); },
        deleteDictation: function(dictationId) { return callResult("deleteDictation", dictationId); },
        purgeHistory: function() { return callResult("purgeHistory"); },
        addMode: function(name) { return callResult("addMode", name || "New Mode"); },
        deleteMode: function(modeId) { return callResult("deleteMode", modeId); },
        updateMode: function(modeId, patch) { return callResult("updateMode", modeId, JSON.stringify(patch)); },
        addAutoRule: function(modeId, type, value, priority) {
          return callResult("addAutoRule", modeId, type, value, priority || 0);
        },
        deleteAutoRule: function(ruleId) { return callResult("deleteAutoRule", ruleId); },
        setShortcut: function(name, value) { return callResult("setShortcut", name, value); },
        setShortcutCaptureActive: function(active) { return callResult("setShortcutCaptureActive", !!active); },
        shortcutModifierState: function() { return callResult("shortcutModifierState"); },
        setSetting: function(section, key, value) {
          return callResult("setSetting", section, key, JSON.stringify(value));
        },
        setApiKey: function(service, value) { return callResult("setApiKey", service, value || ""); },
        deleteApiKey: function(service) { return callResult("deleteApiKey", service); },
        testApiKey: function(service) { return callResult("testApiKey", service); },
        checkForUpdates: function() { return callResult("checkForUpdates"); },
        installUpdate: function() { return callResult("installUpdate"); }
      };
      b.engineStateChanged.connect(function(state) {
        window.dispatchEvent(new CustomEvent("whisperer:engineState", { detail: state }));
      });
      b.settingsChanged.connect(function(snapshot) {
        window.dispatchEvent(new CustomEvent("whisperer:settings", { detail: snapshot }));
      });
      window.dispatchEvent(new Event("whisperer:ready"));
    });
  }
  if (window.QWebChannel) {
    install();
    return;
  }
  var script = document.createElement("script");
  script.src = "qrc:///qtwebchannel/qwebchannel.js";
  script.onload = install;
  document.head.appendChild(script);
})();
"""


def _apply_quiet_model_env(env: dict[str, str]) -> None:
    for key, value in QUIET_MODEL_ENV.items():
        env.setdefault(key, value)


class DiagnosticPage(QWebEnginePage):
    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self._window = window

    def javaScriptConsoleMessage(self, level, message: str, line: int, source: str):
        self._window._log_web_ui(f"{level.name} {source}:{line}: {message}")


class MainWindow(QMainWindow):
    """WebEngine host for the React UI."""

    loadingPreviewRequested = pyqtSignal()
    backupTranscriptionFinished = pyqtSignal(str, bool, str, str)
    updateStatusChanged = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Whisperer v{config.VERSION}")
        self.setWindowIcon(QIcon(app_icon_path()))
        if sys.platform == "darwin":
            self.setWindowFlags(Qt.WindowType.Window)
            try:
                self.setUnifiedTitleAndToolBarOnMac(True)
            except Exception:
                pass
        else:
            self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumSize(980, 640)
        self.resize(1280, 840)

        self.settings = load_settings()
        self._show_request_path = os.path.join(get_app_data_dir(), "show-window.request")
        self._last_show_request_mtime = self._read_show_request_mtime()
        self._gpu_options = self._load_gpu_options()
        self._engine_output_queue: queue.Queue[str] = queue.Queue()
        self._engine_output_lines: list[str] = []
        self._engine_state = "stopped"
        self._engine_ready_file = ""
        self._paused = False
        self._force_quitting = False
        self._quit_shortcut: QShortcut | None = None
        self.process: subprocess.Popen | None = None
        self._mic_level_lock = threading.Lock()
        self._mic_level_db = -96.0
        self._mic_level_value = 0.0
        self._mic_level_error = ""
        self._backup_transcription_busy = False
        self._backup_transcription_status = ""
        self._backup_transcription_error = ""
        self._backup_transcription_request_id = ""
        self._backup_transcription_source = ""
        self._update_status = self._default_update_status()
        self._update_install_script = ""
        self._microphone_cache: list[dict[str, str]] = []
        self._microphone_cache_ts = 0.0
        self._input_channel_count_cache: dict[str, tuple[float, int]] = {}
        self._active_mode_cache = "Voice"
        self._active_mode_cache_ts = 0.0
        self._groq_stt_model_cache: tuple[float, list[dict[str, str]]] = (0.0, [])
        self._loading_preview_enabled = self._should_auto_start_engine()
        self._loading_preview_hotkeys: list = []
        self._loading_preview_locked = False
        self._shortcut_capture_active = False
        self._loading_preview_overlay = WaveformOverlay()
        self._loading_preview_overlay.open_ui_requested.connect(self.show_window)
        self._loading_preview_overlay.force_stop_requested.connect(self.stop_engine)
        self._loading_preview_hide_timer = QTimer(self)
        self._loading_preview_hide_timer.setSingleShot(True)
        self._loading_preview_hide_timer.timeout.connect(self._hide_loading_preview)
        self._loading_preview_release_timer = QTimer(self)
        self._loading_preview_release_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._loading_preview_release_timer.timeout.connect(self._check_loading_preview_release)
        self._backup_transcription_timeout_timer = QTimer(self)
        self._backup_transcription_timeout_timer.setSingleShot(True)
        self._backup_transcription_timeout_timer.timeout.connect(self._on_backup_transcription_timeout)
        self.loadingPreviewRequested.connect(self._show_loading_preview)
        self.backupTranscriptionFinished.connect(self._finish_last_dictation_transcription)
        self.updateStatusChanged.connect(self._apply_update_status)
        if self._loading_preview_enabled and not self._shortcut_capture_active:
            self._register_loading_preview_shortcuts()

        self.view = QWebEngineView(self)
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.view.setStyleSheet("")
        self.page = DiagnosticPage(self)
        self.view.setPage(self.page)
        web_settings = self.view.settings()
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        self.view.page().setBackgroundColor(QColor(248, 247, 243))

        central = QWidget(self)
        central.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        central.setStyleSheet("")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.view)
        self.setCentralWidget(central)

        self.bridge = Bridge(self)
        self.channel = QWebChannel(self.view.page())
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)
        self.view.page().loadFinished.connect(self._on_load_finished)
        self.view.load(_react_index_url())

        self.tray = TrayIcon(self)
        self.tray.show()
        if sys.platform == "darwin":
            self._quit_shortcut = QShortcut(QKeySequence.StandardKey.Quit, self)
            self._quit_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            self._quit_shortcut.activated.connect(self.force_quit)
        QTimer.singleShot(0, self._enable_native_shadow)

        self.output_timer = QTimer(self)
        self.output_timer.timeout.connect(self._drain_engine_output)
        self.output_timer.start(40)
        self.show_request_timer = QTimer(self)
        self.show_request_timer.timeout.connect(self._check_show_window_request)
        self.show_request_timer.start(250)
        self.engine_start_timer = QTimer(self)
        self.engine_start_timer.setSingleShot(True)
        self.engine_start_timer.timeout.connect(self.start_engine)

        if self._loading_preview_enabled:
            self.engine_start_timer.start(400)
        QTimer.singleShot(2400, self._auto_check_for_updates)

    def _on_load_finished(self, ok: bool):
        if not ok:
            self._show_web_ui_error("Whisperer UI did not load.", self.view.url().toString())
            return
        self._log_web_ui(f"Loaded {self.view.url().toString()}")
        self.view.page().runJavaScript(_BRIDGE_SHIM)
        QTimer.singleShot(120, self._emit_snapshot)
        QTimer.singleShot(1200, self._verify_web_ui_mounted)

    def _verify_web_ui_mounted(self):
        self.view.page().runJavaScript(
            "(() => document.getElementById('root')?.innerText?.trim().slice(0, 80) || '')()",
            self._handle_web_ui_probe,
        )

    def _handle_web_ui_probe(self, text: str):
        if text:
            self._log_web_ui(f"React mounted: {text!r}")
            return
        self._log_web_ui("React mount probe returned no visible text")
        self._show_web_ui_error("Whisperer UI opened, but the page stayed blank.", self.view.url().toString())

    def _show_web_ui_error(self, title: str, detail: str):
        escaped_title = json.dumps(title)
        escaped_detail = json.dumps(detail)
        self.view.setHtml(
            f"""
            <!doctype html>
            <meta charset="utf-8">
            <body style="margin:0;background:#f8f7f3;color:#24231f;font:14px Segoe UI,system-ui,sans-serif;">
              <div style="padding:28px;max-width:720px;">
                <h1 style="font-size:20px;margin:0 0 12px;">{title}</h1>
                <p style="line-height:1.5;margin:0 0 12px;">Open the log below for the WebEngine details.</p>
                <pre style="white-space:pre-wrap;background:#fff;border:1px solid #ddd7ce;padding:12px;">{detail}</pre>
              </div>
            </body>
            <script>document.querySelector("h1").textContent = {escaped_title}; document.querySelector("pre").textContent = {escaped_detail};</script>
            """,
            QUrl("about:blank"),
        )

    def _log_web_ui(self, message: str):
        try:
            log_root = os.path.join(get_app_data_dir(), "logs")
            os.makedirs(log_root, exist_ok=True)
            with open(os.path.join(log_root, "web-ui.log"), "a", encoding="utf-8") as log_file:
                log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
        except Exception:
            pass

    def _read_show_request_mtime(self) -> float:
        try:
            return os.path.getmtime(self._show_request_path)
        except OSError:
            return 0.0

    def _check_show_window_request(self):
        mtime = self._read_show_request_mtime()
        if mtime <= self._last_show_request_mtime:
            return
        self._last_show_request_mtime = mtime
        self._log_web_ui("External launch requested UI window")
        self.show_window()

    def _should_auto_start_engine(self) -> bool:
        startup = self.settings.get("startup", {})
        perf = self.settings.get("performance", {})
        return bool(startup.get("auto_start_engine", True)) and perf.get("engine_preload", "app_start") != "off"

    def _loading_preview_hotkey_values(self) -> list[str | None]:
        shortcuts = self.settings.get("shortcuts", {})
        return [
            shortcuts.get("dictation") or config.DICTATION_HOTKEY,
            shortcuts.get("toggle_recording"),
        ]

    def _loading_preview_hotkey_is_pressed(self) -> bool:
        for hotkey in self._loading_preview_hotkey_values():
            normalized = _normalize_keyboard_hotkey(hotkey)
            if not normalized:
                continue
            try:
                if hotkeys.is_pressed(normalized):
                    return True
            except Exception:
                continue
        return False

    def _loading_preview_alt_is_pressed(self) -> bool:
        for key in ("alt", "left alt", "right alt", "menu"):
            try:
                if hotkeys.is_pressed(key):
                    return True
            except Exception:
                continue
        return False

    def _request_loading_preview(self):
        self.loadingPreviewRequested.emit()

    def _register_loading_preview_shortcuts(self):
        self._unregister_loading_preview_shortcuts()
        if not self._loading_preview_enabled or self._engine_state == "running":
            return

        seen: set[str] = set()
        for hotkey in self._loading_preview_hotkey_values():
            normalized = _normalize_keyboard_hotkey(hotkey)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            try:
                handle = hotkeys.add_hotkey(normalized, self._request_loading_preview, suppress=False)
                self._loading_preview_hotkeys.append(handle)
            except Exception:
                pass

    def _unregister_loading_preview_shortcuts(self):
        if not self._loading_preview_hotkeys:
            return
        for handle in self._loading_preview_hotkeys:
            try:
                hotkeys.remove_hotkey(handle)
            except Exception:
                pass
        self._loading_preview_hotkeys.clear()

    def _show_loading_preview(self):
        if not self._loading_preview_enabled or self._engine_state == "running":
            return
        self._loading_preview_overlay.show_model_loading()
        alt_pressed = self._loading_preview_alt_is_pressed()
        if alt_pressed:
            self._loading_preview_locked = True
        self._loading_preview_overlay.set_locked(self._loading_preview_locked or alt_pressed)
        if self._loading_preview_locked:
            self._loading_preview_hide_timer.stop()
        else:
            self._loading_preview_hide_timer.start(LOADING_PREVIEW_HIDE_MS)
        if not self._loading_preview_release_timer.isActive():
            self._loading_preview_release_timer.start(LOADING_RELEASE_POLL_MS)

    def _finish_loading_preview(self):
        self._loading_preview_hide_timer.stop()
        if not self._loading_preview_overlay.isVisible():
            return
        self._loading_preview_overlay.finish_model_loading()
        self._loading_preview_hide_timer.start(LOADING_READY_MORPH_HIDE_MS)
        if not self._loading_preview_release_timer.isActive():
            self._loading_preview_release_timer.start(LOADING_RELEASE_POLL_MS)

    def _check_loading_preview_release(self):
        if not self._loading_preview_overlay.isVisible():
            self._loading_preview_release_timer.stop()
            return
        alt_pressed = self._loading_preview_alt_is_pressed()
        if alt_pressed:
            self._loading_preview_locked = True
        locked = self._loading_preview_locked or alt_pressed
        self._loading_preview_overlay.set_locked(locked)
        if locked:
            self._loading_preview_hide_timer.stop()
            return
        if not self._loading_preview_hotkey_is_pressed():
            self._loading_preview_release_timer.stop()
            self._hide_loading_preview()

    def _hide_loading_preview(self):
        self._loading_preview_hide_timer.stop()
        if self._loading_preview_overlay.isVisible():
            if self._loading_preview_locked:
                self._loading_preview_overlay.set_locked(True)
                return
            if self._loading_preview_hotkey_is_pressed():
                self._loading_preview_hide_timer.start(LOADING_HOTKEY_RELEASE_RETRY_MS)
                return
            if self._loading_preview_overlay.is_interacting():
                self._loading_preview_hide_timer.start(LOADING_INTERACTION_HIDE_RETRY_MS)
                return
            if self._loading_preview_alt_is_pressed():
                self._loading_preview_overlay.set_locked(True)
                return
            self._loading_preview_overlay.set_locked(False)
            self._loading_preview_release_timer.stop()
            self._loading_preview_overlay.fade_out()

    def _emit_snapshot(self):
        snapshot = self.snapshot_json()
        self.bridge.settingsChanged.emit(snapshot)
        self.bridge.engineStateChanged.emit(self._engine_state)

    def _set_engine_state(self, state: str):
        if state == self._engine_state:
            return
        self._engine_state = state
        if state == "running":
            if self._engine_ready_file:
                try:
                    os.remove(self._engine_ready_file)
                except OSError:
                    pass
                self._engine_ready_file = ""
            self._loading_preview_enabled = False
            if self._loading_preview_overlay.isVisible() and self._loading_preview_hotkey_is_pressed():
                self._loading_preview_overlay.hide_now()
            else:
                self._finish_loading_preview()
            self._unregister_loading_preview_shortcuts()
        elif state == "loading":
            self._loading_preview_enabled = True
            self._register_loading_preview_shortcuts()
        elif state == "stopped":
            self._loading_preview_enabled = False
            self._loading_preview_locked = False
            self._loading_preview_overlay.set_locked(False)
            self._loading_preview_release_timer.stop()
            self._hide_loading_preview()
            self._unregister_loading_preview_shortcuts()
        self.bridge.engineStateChanged.emit(state)
        try:
            self.tray.set_status(state)
        except Exception:
            pass
        self._emit_snapshot()

    def engine_state(self) -> str:
        return self._engine_state

    def snapshot_json(self) -> str:
        self.settings = load_settings()
        self.settings.setdefault("startup", {})["launch_on_login"] = self._launch_on_login_enabled()
        microphones = self._load_microphone_options()
        selected_microphone = self._selected_microphone_value(microphones)
        active_mode = self._dashboard_active_mode()
        active_provider = (getattr(active_mode, "stt_provider", None) or "local").strip() or "local"
        model_options = self._model_options_for_provider(active_provider)
        snapshot = {
            "version": config.VERSION,
            "engineState": self._engine_state,
            "settings": self.settings,
            "models": model_options,
            "gpus": self._runtime_options(),
            "microphones": microphones,
            "inputChannels": self._load_input_channel_options(selected_microphone),
            "selectedModel": self._selected_model_for_provider(active_provider, active_mode, model_options),
            "selectedGpu": (
                GROQ_RUNTIME_VALUE
                if active_provider == "groq_whisper"
                else NVIDIA_NIM_RUNTIME_VALUE
                if active_provider == "nvidia_nim_parakeet"
                else str(self.settings.get("startup", {}).get("gpu_device", GPU_AUTO_VALUE))
            ),
            "selectedMicrophone": selected_microphone,
            "selectedInputChannel": str(self.settings.get("audio", {}).get("input_channel", 0) or 0),
            "activeMode": self._active_mode_name(),
            "shortcuts": self._shortcut_payload(),
            "micLevel": self._mic_level_payload(),
            "dictationBackup": self._dictation_backup_payload(),
            "apiKeys": self._api_key_payload(),
            "updateStatus": self._update_status_payload(),
        }
        return json.dumps(snapshot, separators=(",", ":"))

    def mic_level_json(self) -> str:
        return json.dumps(self._mic_level_payload(), separators=(",", ":"))

    def vocabulary_snapshot_json(self) -> str:
        return json.dumps({"vocabulary": self._vocabulary_payload()}, separators=(",", ":"))

    def history_snapshot_json(self) -> str:
        return json.dumps({"history": self._history_payload()}, separators=(",", ":"))

    def modes_snapshot_json(self) -> str:
        return json.dumps({"modesData": self._modes_payload()}, separators=(",", ":"))

    def _save_and_emit(self) -> str:
        save_settings(self.settings)
        snapshot = self.snapshot_json()
        self.bridge.settingsChanged.emit(snapshot)
        return snapshot

    def _emit_key_snapshot(self) -> str:
        snapshot = self.snapshot_json()
        self.bridge.settingsChanged.emit(snapshot)
        return snapshot

    def _api_key_payload(self) -> dict[str, bool]:
        try:
            from core.secrets import get_key

            return {service: bool(get_key(service)) for service in API_KEY_SERVICES}
        except Exception:
            return {service: False for service in API_KEY_SERVICES}

    def _default_update_status(self) -> dict[str, Any]:
        return {
            "state": "idle",
            "busy": False,
            "message": "Updates have not been checked yet.",
            "currentVersion": config.VERSION,
            "latestVersion": "",
            "latestTag": "",
            "checkedAt": "",
            "releaseUrl": UPDATE_RELEASES_URL,
            "assetName": "",
            "assetUrl": "",
            "updateAvailable": False,
        }

    def _update_status_payload(self) -> dict[str, Any]:
        payload = dict(self._update_status)
        payload["currentVersion"] = config.VERSION
        payload.setdefault("releaseUrl", UPDATE_RELEASES_URL)
        payload.setdefault("busy", False)
        payload.setdefault("updateAvailable", False)
        return payload

    def _set_update_status(self, **updates: Any) -> str:
        self._update_status.update(updates)
        self._update_status["currentVersion"] = config.VERSION
        snapshot = self.snapshot_json()
        self.bridge.settingsChanged.emit(snapshot)
        return snapshot

    def _apply_update_status(self, payload_json: str):
        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            payload = {"state": "error", "busy": False, "message": "Could not read update status."}
        self._update_status.update(payload)
        self._update_status["currentVersion"] = config.VERSION
        self._emit_snapshot()

    def _auto_check_for_updates(self):
        state = str(self._update_status.get("state") or "")
        if state in {"checking", "installing"}:
            return
        self.check_for_updates(automatic=True)

    def check_for_updates(self, automatic: bool = False) -> str:
        if str(self._update_status.get("state") or "") == "checking":
            return self.snapshot_json()
        self._set_update_status(
            state="checking",
            busy=True,
            message="Checking GitHub releases...",
            checkedAt="",
            updateAvailable=False,
        )
        threading.Thread(target=self._check_for_updates_worker, args=(automatic,), daemon=True).start()
        return self.snapshot_json()

    def _github_json(self, url: str) -> Any:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"Whisperer/{config.VERSION}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _fetch_latest_release(self) -> dict[str, Any]:
        try:
            payload = self._github_json(UPDATE_LATEST_API_URL)
            if isinstance(payload, dict):
                return payload
        except urllib.error.HTTPError as exc:
            if exc.code not in (403, 404):
                raise
        releases = self._github_json(UPDATE_ALL_RELEASES_API_URL)
        if not isinstance(releases, list):
            raise RuntimeError("GitHub returned an unexpected release list.")
        for release in releases:
            if not isinstance(release, dict):
                continue
            if release.get("draft") or release.get("prerelease"):
                continue
            return release
        raise RuntimeError("No public Whisperer Mac releases were found.")

    def _status_from_release(self, release: dict[str, Any]) -> dict[str, Any]:
        tag = str(release.get("tag_name") or "")
        name = str(release.get("name") or "")
        latest_version = _release_version(tag, name)
        release_url = str(release.get("html_url") or UPDATE_RELEASES_URL)
        asset = _select_macos_release_asset(release)
        update_available = bool(latest_version and _is_newer_release(config.VERSION, latest_version))
        asset_name = str(asset.get("name") or "") if asset else ""
        asset_url = str(asset.get("browser_download_url") or "") if asset else ""
        if update_available and asset_url:
            state = "available"
            message = f"Whisperer {latest_version} is available."
        elif update_available:
            state = "error"
            message = f"Whisperer {latest_version} is available, but the release has no macOS DMG."
        else:
            state = "up_to_date"
            message = "Whisperer is up to date."
        return {
            "state": state,
            "busy": False,
            "message": message,
            "currentVersion": config.VERSION,
            "latestVersion": latest_version,
            "latestTag": tag,
            "checkedAt": _utc_timestamp(),
            "releaseUrl": release_url,
            "assetName": asset_name,
            "assetUrl": asset_url,
            "updateAvailable": update_available and bool(asset_url),
        }

    def _check_for_updates_worker(self, automatic: bool):
        try:
            status = self._status_from_release(self._fetch_latest_release())
        except Exception as exc:
            message = "Could not check for updates."
            if not automatic:
                message = f"{message} {exc}"
            status = {
                "state": "error",
                "busy": False,
                "message": message,
                "checkedAt": _utc_timestamp(),
                "updateAvailable": False,
                "releaseUrl": UPDATE_RELEASES_URL,
            }
        self.updateStatusChanged.emit(json.dumps(status, separators=(",", ":")))

    def install_update(self) -> str:
        if str(self._update_status.get("state") or "") == "installing":
            return self.snapshot_json()
        if sys.platform != "darwin":
            return self._set_update_status(
                state="error",
                busy=False,
                message="Automatic updates are only available in the macOS app.",
            )
        asset_url = str(self._update_status.get("assetUrl") or "")
        if not self._update_status.get("updateAvailable") or not asset_url:
            return self._set_update_status(
                state="error",
                busy=False,
                message="No downloadable update is available yet. Check for updates first.",
            )
        app_bundle = _app_bundle_path()
        if not app_bundle or not os.path.isdir(app_bundle):
            return self._set_update_status(
                state="error",
                busy=False,
                message="Whisperer needs to be running from a macOS app bundle to update itself.",
            )
        try:
            script_path = self._write_update_installer_script(app_bundle, asset_url)
            subprocess.Popen(
                ["/bin/zsh", script_path],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as exc:
            return self._set_update_status(
                state="error",
                busy=False,
                message=f"Could not start the updater. {exc}",
            )
        asset_name = str(self._update_status.get("assetName") or "the latest release")
        return self._set_update_status(
            state="installing",
            busy=True,
            message=f"Downloading {asset_name}. Whisperer will restart when the update is installed.",
        )

    def _write_update_installer_script(self, app_bundle: str, asset_url: str) -> str:
        log_dir = os.path.join(get_app_data_dir(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "updater.log")
        script_dir = tempfile.mkdtemp(prefix="whisperer-update-", dir=tempfile.gettempdir())
        script_path = os.path.join(script_dir, "install_update.zsh")
        pid = os.getpid()
        quoted_url = shlex.quote(asset_url)
        quoted_app = shlex.quote(app_bundle)
        quoted_log = shlex.quote(log_path)
        quoted_user_data = shlex.quote(get_app_data_dir())
        app_name = shlex.quote(os.path.splitext(os.path.basename(app_bundle))[0] or "Whisperer")
        script = f"""#!/bin/zsh
set -euo pipefail
URL={quoted_url}
APP_BUNDLE={quoted_app}
APP_NAME={app_name}
USER_DATA_DIR={quoted_user_data}
UI_PID={pid}
LOG={quoted_log}
TMP_DIR="$(mktemp -d /tmp/whisperer-update.XXXXXX)"
MOUNT_DIR="$TMP_DIR/mount"
DMG_PATH="$TMP_DIR/update.dmg"
trap 'hdiutil detach "$MOUNT_DIR" >/dev/null 2>&1 || true; rm -rf "$TMP_DIR"' EXIT
mkdir -p "$MOUNT_DIR"
echo "$(date) starting update from $URL" >> "$LOG"
echo "$(date) preserving user data at $USER_DATA_DIR" >> "$LOG"
if [[ "$APP_BUNDLE" == "$USER_DATA_DIR"/* ]]; then
  echo "$(date) refusing update because app bundle is inside user data directory" >> "$LOG"
  exit 1
fi
/usr/bin/curl --fail --location --retry 2 --connect-timeout 15 --max-time 600 --output "$DMG_PATH" "$URL" >> "$LOG" 2>&1
/usr/bin/hdiutil attach "$DMG_PATH" -nobrowse -readonly -mountpoint "$MOUNT_DIR" >> "$LOG" 2>&1
SRC_APP="$(/usr/bin/find "$MOUNT_DIR" -maxdepth 3 -name 'Whisperer.app' -type d -print -quit)"
if [[ -z "$SRC_APP" ]]; then
  echo "$(date) no Whisperer.app found in DMG" >> "$LOG"
  exit 1
fi
/usr/bin/ditto "$SRC_APP" "$TMP_DIR/Whisperer.app" >> "$LOG" 2>&1
/usr/bin/osascript -e "tell application \\"$APP_NAME\\" to quit" >/dev/null 2>&1 || true
for i in {{1..40}}; do
  /bin/kill -0 "$UI_PID" >/dev/null 2>&1 || break
  /bin/sleep 0.25
done
/bin/kill -9 "$UI_PID" >/dev/null 2>&1 || true
/usr/bin/pkill -9 -f "$APP_BUNDLE/Contents/MacOS/Whisperer" >/dev/null 2>&1 || true
/bin/rm -rf "$APP_BUNDLE"
/usr/bin/ditto "$TMP_DIR/Whisperer.app" "$APP_BUNDLE" >> "$LOG" 2>&1
/usr/bin/xattr -dr com.apple.quarantine "$APP_BUNDLE" >/dev/null 2>&1 || true
/usr/bin/open -a "$APP_BUNDLE"
echo "$(date) update installed" >> "$LOG"
"""
        with open(script_path, "w", encoding="utf-8") as handle:
            handle.write(script)
        os.chmod(script_path, 0o700)
        self._update_install_script = script_path
        return script_path

    def _dictation_backup_payload(self) -> dict[str, Any]:
        try:
            from core.dictation_backup import last_dictation_backup_metadata

            payload = last_dictation_backup_metadata()
        except Exception:
            payload = {"available": False, "sizeBytes": 0, "durationSeconds": 0, "modifiedAt": ""}
        payload["busy"] = self._backup_transcription_busy
        payload["status"] = self._backup_transcription_status
        payload["error"] = self._backup_transcription_error
        return payload

    def transcribe_last_dictation(self) -> str:
        if self._backup_transcription_busy:
            return self.snapshot_json()
        payload = self._dictation_backup_payload()
        if not payload.get("available"):
            self._backup_transcription_status = ""
            self._backup_transcription_error = "No last dictation backup is available yet."
            snapshot = self.snapshot_json()
            self.bridge.settingsChanged.emit(snapshot)
            return snapshot
        if self.process and self.process.poll() is None and self._engine_state != "running":
            self._backup_transcription_status = ""
            self._backup_transcription_error = "The dictation engine is still loading. Try again once it says Engine ready."
            snapshot = self.snapshot_json()
            self.bridge.settingsChanged.emit(snapshot)
            return snapshot

        self._backup_transcription_busy = True
        self._backup_transcription_status = "Transcribing last dictation..."
        self._backup_transcription_error = ""
        request_id = str(int(time.time() * 1000))
        self._backup_transcription_request_id = request_id
        snapshot = self.snapshot_json()
        self.bridge.settingsChanged.emit(snapshot)
        self._backup_transcription_timeout_timer.start(180000)
        if self._send_engine_backup_transcription_request(request_id):
            self._backup_transcription_source = "engine"
        else:
            self._backup_transcription_source = "subprocess"
            threading.Thread(target=self._transcribe_last_dictation_worker, args=(request_id,), daemon=True).start()
        return snapshot

    def _send_engine_backup_transcription_request(self, request_id: str) -> bool:
        return self._send_engine_command(
            {"command": "transcribe_last_dictation", "requestId": request_id},
            require_running=True,
        )

    def _send_engine_command(self, payload: dict[str, Any], require_running: bool = False) -> bool:
        if (
            (require_running and self._engine_state != "running")
            or not self.process
            or self.process.poll() is not None
            or not self.process.stdin
        ):
            return False
        try:
            command = json.dumps(payload, separators=(",", ":"))
            self.process.stdin.write(command + "\n")
            self.process.stdin.flush()
            return True
        except Exception:
            return False

    def _transcribe_last_dictation_worker(self, request_id: str):
        try:
            text = self._run_last_dictation_transcription()
            self.backupTranscriptionFinished.emit(request_id, True, text, "")
        except subprocess.TimeoutExpired:
            self.backupTranscriptionFinished.emit(
                request_id,
                False,
                "",
                "Last dictation transcription timed out. The engine may still be loading; try again once it is ready.",
            )
        except Exception as exc:
            self.backupTranscriptionFinished.emit(request_id, False, "", str(exc))

    def _finish_last_dictation_transcription(self, request_id: str, ok: bool, text: str, error: str):
        if request_id and request_id != self._backup_transcription_request_id:
            return
        self._backup_transcription_timeout_timer.stop()
        self._backup_transcription_busy = False
        self._backup_transcription_request_id = ""
        self._backup_transcription_source = ""
        if ok and text.strip():
            QApplication.clipboard().setText(text.strip())
            self._backup_transcription_status = "Copied last dictation to clipboard."
            self._backup_transcription_error = ""
        elif ok:
            self._backup_transcription_status = ""
            self._backup_transcription_error = "The backup did not contain transcribable speech."
        else:
            self._backup_transcription_status = ""
            self._backup_transcription_error = error or "Could not transcribe the last dictation."
        self._emit_snapshot()

    def _on_backup_transcription_timeout(self):
        if not self._backup_transcription_busy:
            return
        request_id = self._backup_transcription_request_id
        self._finish_last_dictation_transcription(
            request_id,
            False,
            "",
            "Last dictation transcription took too long and was stopped. Try again after the engine is ready.",
        )

    def _engine_python_context(self) -> tuple[str, str, dict[str, str]]:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if getattr(sys, "frozen", False):
            source_root = self._frozen_engine_source_root()
            python_exe = self._external_engine_python()
            if not source_root or not python_exe:
                raise RuntimeError("The external Python engine runtime could not be found.")
        else:
            source_root = project_root
            python_exe = sys.executable

        env = os.environ.copy()
        env["PYTHONPATH"] = source_root + os.pathsep + env.get("PYTHONPATH", "")
        env["WHISPERER_PROJECT_ROOT"] = source_root
        env["PYTHONUTF8"] = "1"
        env["WHISPERER_MODEL"] = self._current_model_value()
        _apply_quiet_model_env(env)
        self._apply_engine_gpu_env(env)
        return python_exe, source_root, env

    def _run_last_dictation_transcription(self) -> str:
        python_exe, source_root, env = self._engine_python_context()
        script = r'''
import json
import os
import sys

source_root = os.environ.get("WHISPERER_PROJECT_ROOT", "")
if source_root and source_root not in sys.path:
    sys.path.insert(0, source_root)

import config

model = os.environ.get("WHISPERER_MODEL", "")
if model:
    config.WHISPER_MODEL_SIZE = model

from core.dictation_backup import finalize_last_dictation_wav, load_last_dictation_audio
from core.dictionary import apply_replacements, get_prompt_words
from core.formatter import format_transcription
from core.transcriber import transcribe

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
print("WHISPERER_BACKUP_RESULT " + json.dumps({"text": final_text, "raw": raw_text}, ensure_ascii=False), flush=True)
'''
        result = subprocess.run(
            [python_exe, "-u", "-c", script],
            cwd=source_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        output = result.stdout or ""
        clean_lines = [line for line in output.splitlines() if not self._is_noisy_engine_line(line)]
        if result.returncode != 0:
            tail = "\n".join(clean_lines[-8:]).strip()
            raise RuntimeError(tail or "Backup transcription failed.")
        for line in reversed(clean_lines):
            if line.startswith("WHISPERER_BACKUP_RESULT "):
                payload = json.loads(line.split(" ", 1)[1])
                return str(payload.get("text") or payload.get("raw") or "").strip()
        tail = "\n".join(clean_lines[-8:]).strip()
        if tail:
            raise RuntimeError(tail)
        raise RuntimeError("Backup transcription finished without returning text.")

    def _dashboard_active_mode(self):
        try:
            from core.modes import get_mode_by_name

            return get_mode_by_name(self._active_mode_cache) or get_mode_by_name("Voice")
        except Exception:
            return None

    def _runtime_options(self) -> list[dict[str, Any]]:
        options = [{"value": value, "label": label} for label, value in self._gpu_options]
        options.append({"value": "__cloud_runtime_divider__", "label": "", "divider": True})
        options.append({"value": GROQ_RUNTIME_VALUE, "label": "Groq API", "hint": "Cloud"})
        options.append({"value": NVIDIA_NIM_RUNTIME_VALUE, "label": "NVIDIA Parakeet API", "hint": "Cloud"})
        return options

    def _groq_stt_fallback_options(self) -> list[dict[str, str]]:
        return [
            {
                "value": model_id,
                "label": f"{meta['label']} - {meta['price']}",
                "hint": meta["hint"],
            }
            for model_id, meta in GROQ_STT_MODEL_META.items()
        ]

    def _load_groq_stt_model_options(self) -> list[dict[str, str]]:
        cached_at, cached_options = self._groq_stt_model_cache
        if cached_options and time.monotonic() - cached_at < 300:
            return cached_options

        fallback = self._groq_stt_fallback_options()
        try:
            from core.secrets import get_key

            key = get_key("groq")
        except Exception:
            key = None
        if not key:
            return fallback

        try:
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/models",
                headers={
                    "Authorization": f"Bearer {key}",
                    "User-Agent": "Whisperer/6.0.0",
                    "Accept": "application/json",
                },
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            available = {
                str(item.get("id") or "")
                for item in payload.get("data", [])
                if isinstance(item, dict) and item.get("active", True)
            }
            options = [
                option
                for option in fallback
                if option["value"] in available
            ]
            if options:
                self._groq_stt_model_cache = (time.monotonic(), options)
                return options
        except Exception:
            pass
        return cached_options or fallback

    def _model_options_for_provider(self, provider: str) -> list[dict[str, str]]:
        if provider == "groq_whisper":
            return self._load_groq_stt_model_options()
        if provider == "nvidia_nim_parakeet":
            return NVIDIA_NIM_MODEL_OPTIONS
        return _available_model_options()

    def _selected_model_for_provider(self, provider: str, active_mode, options: list[dict[str, str]]) -> str:
        valid_values = {item["value"] for item in options}
        if provider == "groq_whisper":
            value = (getattr(active_mode, "stt_model", None) or "whisper-large-v3-turbo").strip()
            return value if value in valid_values else "whisper-large-v3-turbo"
        if provider == "nvidia_nim_parakeet":
            value = _nvidia_nim_model_value(getattr(active_mode, "stt_model", None))
            return value if value in valid_values else NVIDIA_NIM_DEFAULT_MODEL
        return self._current_model_value()

    def _update_dashboard_active_stt(self, provider: str, model: str | None = None) -> bool:
        try:
            from core.modes import get_mode_by_name, update_mode

            mode = get_mode_by_name(self._active_mode_cache) or get_mode_by_name("Voice")
            if not mode or mode.id is None:
                return False
            update_mode(int(mode.id), stt_provider=provider, stt_model=model or "")
            return True
        except Exception:
            return False

    def set_model(self, value: str) -> str:
        active_mode = self._dashboard_active_mode()
        provider = (getattr(active_mode, "stt_provider", None) or "local").strip() or "local"
        if provider == "groq_whisper":
            options = self._load_groq_stt_model_options()
            valid_values = {item["value"] for item in options}
            if value not in valid_values:
                value = "whisper-large-v3-turbo"
            self._update_dashboard_active_stt("groq_whisper", value)
            snapshot = self.snapshot_json()
            self.bridge.settingsChanged.emit(snapshot)
            return snapshot

        if provider == "nvidia_nim_parakeet":
            options = NVIDIA_NIM_MODEL_OPTIONS
            valid_values = {item["value"] for item in options}
            value = _nvidia_nim_model_value(value)
            if value not in valid_values:
                value = NVIDIA_NIM_DEFAULT_MODEL
            self._update_dashboard_active_stt("nvidia_nim_parakeet", value)
            snapshot = self.snapshot_json()
            self.bridge.settingsChanged.emit(snapshot)
            return snapshot

        options = _available_model_options()
        valid_values = {item["value"] for item in options}
        if value not in valid_values:
            value = options[0]["value"]
        self.settings.setdefault("startup", {})["default_model"] = value
        snapshot = self._save_and_emit()
        if self.process and self.process.poll() is None:
            self.restart_engine()
        return snapshot

    def set_gpu(self, value: str) -> str:
        if value == GROQ_RUNTIME_VALUE:
            active_mode = self._dashboard_active_mode()
            current_model = (getattr(active_mode, "stt_model", None) or "").strip()
            valid_groq_models = {item["value"] for item in self._load_groq_stt_model_options()}
            if current_model not in valid_groq_models:
                current_model = "whisper-large-v3-turbo"
            self._update_dashboard_active_stt("groq_whisper", current_model)
            snapshot = self.snapshot_json()
            self.bridge.settingsChanged.emit(snapshot)
            return snapshot

        if value == NVIDIA_NIM_RUNTIME_VALUE:
            active_mode = self._dashboard_active_mode()
            current_model = _nvidia_nim_model_value(getattr(active_mode, "stt_model", None))
            valid_nvidia_models = {item["value"] for item in NVIDIA_NIM_MODEL_OPTIONS}
            if current_model not in valid_nvidia_models:
                current_model = NVIDIA_NIM_DEFAULT_MODEL
            self._update_dashboard_active_stt("nvidia_nim_parakeet", current_model)
            snapshot = self.snapshot_json()
            self.bridge.settingsChanged.emit(snapshot)
            return snapshot

        valid_values = {gpu_value for _label, gpu_value in self._gpu_options}
        if value not in valid_values:
            value = GPU_AUTO_VALUE
        self._update_dashboard_active_stt("local", "")
        self.settings.setdefault("startup", {})["gpu_device"] = value
        snapshot = self._save_and_emit()
        if self.process and self.process.poll() is None:
            self.restart_engine()
        return snapshot

    def set_microphone(self, value: str) -> str:
        audio = self.settings.setdefault("audio", {})
        if value == "default":
            audio["input_device"] = None
            audio["input_device_name"] = None
        else:
            try:
                index = int(value)
                audio["input_device"] = index
                audio["input_device_name"] = self._device_name(index)
            except (TypeError, ValueError):
                audio["input_device"] = None
                audio["input_device_name"] = None
        max_channels = max(1, self._input_channel_count(value))
        try:
            channel = int(audio.get("input_channel", 0) or 0)
        except (TypeError, ValueError):
            channel = 0
        audio["input_channel"] = max(0, min(channel, max_channels - 1))
        return self._save_and_emit()

    def set_input_channel(self, value: str) -> str:
        try:
            channel = max(0, int(value))
        except (TypeError, ValueError):
            channel = 0
        selected = self._selected_microphone_value(self._load_microphone_options())
        max_channels = max(1, self._input_channel_count(selected))
        self.settings.setdefault("audio", {})["input_channel"] = min(channel, max_channels - 1)
        return self._save_and_emit()

    def set_setting(self, section: str, key: str, value: Any) -> str:
        if not section or not key:
            return self.snapshot_json()
        if section == "startup" and key == "launch_on_login":
            value = bool(value)
            try:
                from scripts.launch_on_login import set_launch_on_login

                set_launch_on_login(value)
            except Exception:
                pass
        if section == "audio" and key == "ducking_percent":
            try:
                value = max(0, min(100, int(round(int(value) / 25)) * 25))
            except (TypeError, ValueError):
                value = 75
        if section == "audio" and key == "ducking_enabled":
            value = bool(value)
        self.settings.setdefault(section, {})[key] = value
        snapshot = self._save_and_emit()
        if section == "performance" and key == "engine_preload" and value == "off":
            self.stop_engine()
        if section == "startup" and key == "auto_start_engine":
            if not value:
                self.stop_engine()
            elif self._should_auto_start_engine():
                self.start_engine()
        return snapshot

    def set_api_key(self, service: str, value: str) -> str:
        service = (service or "").strip()
        if service not in API_KEY_SERVICES:
            return self.snapshot_json()
        value = (value or "").strip()
        try:
            from core.secrets import delete_key, set_key

            if value:
                set_key(service, value)
            else:
                delete_key(service)
        except Exception:
            pass
        return self._emit_key_snapshot()

    def delete_api_key(self, service: str) -> str:
        service = (service or "").strip()
        if service not in API_KEY_SERVICES:
            return self.snapshot_json()
        try:
            from core.secrets import delete_key

            delete_key(service)
        except Exception:
            pass
        return self._emit_key_snapshot()

    def _test_api_key_result(self, service: str) -> dict[str, Any]:
        service = (service or "").strip()
        label = API_KEY_SERVICES.get(service, service or "Provider")
        if service not in API_KEY_SERVICES:
            return {"service": service, "ok": False, "message": "Unknown provider."}

        try:
            from core.secrets import get_key

            key = get_key(service)
        except Exception:
            key = None
        if not key:
            return {"service": service, "ok": False, "message": f"No {label} key is saved yet."}

        checks = {
            "groq": (
                "https://api.groq.com/openai/v1/models",
                {
                    "Authorization": f"Bearer {key}",
                    "User-Agent": "Whisperer/6.0.0",
                    "Accept": "application/json",
                },
            ),
            "openai": ("https://api.openai.com/v1/models", {"Authorization": f"Bearer {key}"}),
            "deepgram": ("https://api.deepgram.com/v1/projects", {"Authorization": f"Token {key}"}),
        }
        check = checks.get(service)
        if check is None:
            return {"service": service, "ok": False, "message": f"{label} does not have a built-in key test yet."}

        url, headers = check
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if 200 <= int(getattr(resp, "status", 200)) < 300:
                    return {"service": service, "ok": True, "message": f"{label} key is working."}
        except urllib.error.HTTPError as exc:
            return {
                "service": service,
                "ok": False,
                "message": f"{label} rejected the key ({exc.code}).",
            }
        except Exception as exc:
            return {"service": service, "ok": False, "message": f"Could not reach {label}: {exc}"}
        return {"service": service, "ok": False, "message": f"{label} returned an unexpected response."}

    def test_api_key(self, service: str) -> str:
        payload = {
            "apiKeyTest": self._test_api_key_result(service),
            "apiKeys": self._api_key_payload(),
        }
        return json.dumps(payload, separators=(",", ":"))

    def set_shortcut(self, name: str, value: str) -> str:
        name = (name or "").strip()
        value = _normalize_keyboard_hotkey((value or "").strip().lower()) or ""
        if not name:
            return self.snapshot_json()
        self.settings = load_settings()
        shortcuts = self.settings.setdefault("shortcuts", {})
        shortcuts[name] = value or None
        snapshot = self._save_and_emit()
        if self._loading_preview_enabled and self._engine_state != "running":
            self._register_loading_preview_shortcuts()
        if self.process and self.process.poll() is None:
            self.restart_engine()
        return snapshot

    def set_shortcut_capture_active(self, active: bool) -> str:
        active = bool(active)
        self._shortcut_capture_active = active
        if active:
            self._unregister_loading_preview_shortcuts()
        elif self._loading_preview_enabled and self._engine_state != "running":
            self._register_loading_preview_shortcuts()
        self._send_engine_command({"command": "set_hotkeys_paused", "paused": active})
        return self.snapshot_json()

    def shortcut_modifier_state(self) -> str:
        order = {"ctrl": 0, "alt": 1, "shift": 2, "cmd": 3, "fn": 4}
        modifiers = sorted(hotkeys.pressed_modifiers(), key=lambda value: order.get(value, 99))
        return json.dumps({"modifiers": modifiers}, separators=(",", ":"))

    def _launch_on_login_enabled(self) -> bool:
        try:
            from scripts.launch_on_login import is_launch_on_login_enabled

            return bool(is_launch_on_login_enabled())
        except Exception:
            return bool(self.settings.get("startup", {}).get("launch_on_login", False))

    def _current_model_value(self) -> str:
        options = _available_model_options()
        value = self.settings.get("startup", {}).get("default_model", options[0]["value"])
        valid_values = {item["value"] for item in options}
        return value if value in valid_values else options[0]["value"]

    def _load_gpu_options(self) -> list[tuple[str, str]]:
        if sys.platform == "darwin":
            return [("Auto (Apple Silicon / CPU)", GPU_AUTO_VALUE)]
        options = [("Auto (primary CUDA GPU)", GPU_AUTO_VALUE)]
        try:
            creationflags = 0x08000000 if os.name == "nt" else 0
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=creationflags,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    parts = [part.strip() for part in line.split(",", 2)]
                    if len(parts) != 3:
                        continue
                    index, name, memory_mb = parts
                    try:
                        memory_gb = round(int(memory_mb) / 1024)
                        label = f"GPU {index} - {name} ({memory_gb} GB)"
                    except ValueError:
                        label = f"GPU {index} - {name}"
                    options.append((label, index))
        except Exception:
            pass
        return options

    def _apply_engine_gpu_env(self, env: dict[str, str]):
        gpu_value = str(self.settings.get("startup", {}).get("gpu_device", GPU_AUTO_VALUE))
        if gpu_value and gpu_value != GPU_AUTO_VALUE:
            env["CUDA_VISIBLE_DEVICES"] = gpu_value
        else:
            env.pop("CUDA_VISIBLE_DEVICES", None)

    def _load_microphone_options(self) -> list[dict[str, str]]:
        now = time.monotonic()
        if self._microphone_cache and now - self._microphone_cache_ts < 8.0:
            return list(self._microphone_cache)
        options = [{"value": "default", "label": "System default microphone", "hint": "Auto"}]
        seen: set[str] = set()
        try:
            import sounddevice as sd

            for index, device in enumerate(sd.query_devices()):
                if int(device.get("max_input_channels", 0)) <= 0:
                    continue
                name = str(device.get("name", "")).strip() or f"Input device {index}"
                label = name if name not in seen else f"{name} ({index})"
                seen.add(name)
                channels = int(device.get("max_input_channels", 0))
                options.append({"value": str(index), "label": label, "hint": f"{channels} ch"})
        except Exception:
            pass
        self._microphone_cache = list(options)
        self._microphone_cache_ts = now
        return options

    def _selected_microphone_value(self, options: list[dict[str, str]]) -> str:
        audio = self.settings.get("audio", {})
        selected_index = audio.get("input_device")
        selected_name = audio.get("input_device_name")
        if isinstance(selected_index, int):
            value = str(selected_index)
            if any(option["value"] == value for option in options):
                return value
        if selected_name:
            for option in options:
                if option["label"] == selected_name:
                    return option["value"]
        return "default"

    def _device_name(self, index: int) -> str | None:
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            if 0 <= index < len(devices):
                return str(devices[index].get("name", "")).strip() or None
        except Exception:
            pass
        return None

    def _input_channel_count(self, selected_microphone: str) -> int:
        cache_key = selected_microphone or "default"
        now = time.monotonic()
        cached = self._input_channel_count_cache.get(cache_key)
        if cached and now - cached[0] < 8.0:
            return cached[1]
        try:
            import sounddevice as sd

            if selected_microphone != "default":
                device = sd.query_devices(int(selected_microphone), "input")
            else:
                device = sd.query_devices(kind="input")
            count = max(1, int(device.get("max_input_channels", 1) or 1))
        except Exception:
            count = 1
        self._input_channel_count_cache[cache_key] = (now, count)
        return count

    def _load_input_channel_options(self, selected_microphone: str) -> list[dict[str, str]]:
        count = self._input_channel_count(selected_microphone)
        return [{"value": str(index), "label": f"Channel {index + 1}"} for index in range(count)]

    def _active_mode_name(self) -> str:
        # Keep the dashboard process lightweight. Resolving the active mode needs
        # core.context, which imports OCR/pandas/pyarrow and can stall or crash
        # the Qt host. The engine process owns real active-mode resolution.
        return self._active_mode_cache

    def _shortcut_payload(self) -> dict[str, list[str]]:
        shortcuts = self.settings.get("shortcuts", {})
        return {name: self._hotkey_to_keys(value) for name, value in shortcuts.items() if value}

    def _hotkey_to_keys(self, hotkey: str | None) -> list[str]:
        if not hotkey:
            return []
        labels: list[str] = []
        normalized = _normalize_keyboard_hotkey(hotkey) or ""
        for part in normalized.split("+"):
            key = part.strip()
            lookup = {
                "ctrl": "Ctrl",
                "control": "Ctrl",
                "alt": "Option" if sys.platform == "darwin" else "Alt",
                "option": "Option",
                "shift": "Shift",
                "cmd": "Cmd" if sys.platform == "darwin" else "Windows",
                "fn": "Fn",
                "left windows": "Cmd" if sys.platform == "darwin" else "Left Windows",
                "right windows": "Cmd" if sys.platform == "darwin" else "Right Windows",
                "windows": "Cmd" if sys.platform == "darwin" else "Windows",
                "win": "Cmd" if sys.platform == "darwin" else "Windows",
                "escape": "Esc",
                "page up": "Page Up",
                "page down": "Page Down",
                "space": "Space",
                "enter": "Enter",
                "tab": "Tab",
                "backspace": "Backspace",
                "delete": "Delete",
                "left": "Left",
                "right": "Right",
                "up": "Up",
                "down": "Down",
                "plus": "Plus",
                "minus": "Minus",
                "equals": "Equals",
                "comma": "Comma",
                "period": "Period",
                "slash": "Slash",
                "backslash": "Backslash",
                "semicolon": "Semicolon",
                "quote": "Quote",
                "grave": "Grave",
                "left bracket": "Left Bracket",
                "right bracket": "Right Bracket",
                "exclamation": "Exclamation",
                "at": "At",
                "hash": "Hash",
                "dollar": "Dollar",
                "percent": "Percent",
                "caret": "Caret",
                "ampersand": "Ampersand",
                "asterisk": "Asterisk",
                "left paren": "Left Paren",
                "right paren": "Right Paren",
            }
            labels.append(lookup.get(key.lower(), key[:1].upper() + key[1:]))
        return labels

    def _timing_payload(self) -> dict[str, list[dict[str, Any]]]:
        try:
            from core.perf import timing_summary

            return timing_summary(limit=80)
        except Exception:
            return {"startup": [], "dictation": [], "other": []}

    def _vocabulary_payload(self) -> dict[str, Any]:
        try:
            from core.dictionary import get_replacement_rules, get_word_count, get_words, init_db

            init_db()
            rules = get_replacement_rules(enabled_only=False)
            words = get_words(limit=500)
            return {
                "wordCount": get_word_count(),
                "words": words,
                "rules": rules,
            }
        except Exception as exc:
            return {"wordCount": 0, "words": [], "rules": [], "error": str(exc)}

    def add_vocabulary_word(self, word: str) -> str:
        word = (word or "").strip()
        if word:
            from core.dictionary import add_word

            add_word(word, source="manual")
        return self.vocabulary_snapshot_json()

    def add_replacement_rule(self, match_text: str, replace_with: str) -> str:
        match_text = (match_text or "").strip()
        if match_text:
            from core.dictionary import add_replacement_rule

            add_replacement_rule(match_text, replace_with or "")
        return self.vocabulary_snapshot_json()

    def _history_payload(self) -> dict[str, Any]:
        try:
            from core.history import list_dictations

            rows = list_dictations(limit=220)
            today = time.strftime("%Y-%m-%d")
            items: list[dict[str, Any]] = []
            total_words = 0
            total_ms = 0
            today_count = 0
            for row in rows:
                final_text = row.get("final_text") or ""
                raw_text = row.get("raw_transcript") or ""
                text = final_text or raw_text
                words = len(text.split())
                duration_ms = int(row.get("duration_ms") or 0)
                started_at = str(row.get("started_at") or "")
                error = row.get("error") or ""
                total_words += words
                total_ms += max(0, duration_ms)
                if started_at.startswith(today):
                    today_count += 1
                items.append(
                    {
                        "id": int(row.get("id") or 0),
                        "startedAt": started_at,
                        "app": row.get("app_name") or "Unknown app",
                        "windowTitle": row.get("window_title") or "",
                        "mode": row.get("mode_name") or "Voice",
                        "duration": max(0, round(duration_ms / 1000)),
                        "words": words,
                        "text": text,
                        "rawText": raw_text,
                        "finalText": final_text,
                        "error": error,
                        "status": "error" if error else "ok",
                        "pasteSucceeded": row.get("paste_succeeded"),
                        "pasteMethod": row.get("paste_method") or "",
                        "sttProvider": row.get("stt_provider") or "",
                        "sttModel": row.get("stt_model") or "",
                        "audioPath": row.get("audio_path") or "",
                    }
                )
            return {
                "items": items,
                "totals": {
                    "today": today_count,
                    "words": total_words,
                    "minutes": round(total_ms / 60000),
                },
                "stats": self._history_stats_payload(),
            }
        except Exception as exc:
            return {
                "items": [],
                "totals": {"today": 0, "words": 0, "minutes": 0},
                "stats": self._empty_history_stats(),
                "error": str(exc),
            }

    def _empty_history_stats(self) -> dict[str, Any]:
        days = [date.today() - timedelta(days=offset) for offset in range(6, -1, -1)]
        return {
            "totalDictations": 0,
            "totalWords": 0,
            "totalMinutes": 0,
            "topWords": [],
            "last7Days": [
                {"date": day.isoformat(), "label": day.strftime("%a"), "words": 0, "minutes": 0, "dictations": 0}
                for day in days
            ],
        }

    def _history_stats_payload(self) -> dict[str, Any]:
        try:
            from core.migrations import get_connection

            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT started_at, duration_ms, raw_transcript, final_text, error
                FROM dictations
                ORDER BY started_at ASC
            """)
            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()
        except Exception:
            return self._empty_history_stats()

        stopwords = {
            "the", "and", "that", "this", "with", "for", "you", "your", "are", "was", "were",
            "have", "has", "had", "but", "not", "from", "they", "them", "there", "then", "than",
            "what", "when", "where", "would", "could", "should", "about", "into", "just", "like",
            "over", "because", "really", "very", "can", "will", "all", "our", "out", "now",
        }
        days = [date.today() - timedelta(days=offset) for offset in range(6, -1, -1)]
        daily = {
            day.isoformat(): {"date": day.isoformat(), "label": day.strftime("%a"), "words": 0, "minutes": 0, "dictations": 0}
            for day in days
        }
        word_counts: dict[str, int] = {}
        total_words = 0
        total_ms = 0
        total_dictations = 0

        for row in rows:
            if row.get("error"):
                continue
            text = (row.get("final_text") or row.get("raw_transcript") or "").strip()
            words = re.findall(r"[A-Za-z][A-Za-z'-]{1,}", text.lower())
            word_count = len(words)
            duration_ms = max(0, int(row.get("duration_ms") or 0))
            total_words += word_count
            total_ms += duration_ms
            total_dictations += 1
            for word in words:
                clean = word.strip("'-")
                if len(clean) <= 2 or clean in stopwords:
                    continue
                word_counts[clean] = word_counts.get(clean, 0) + 1
            started_at = str(row.get("started_at") or "")
            day_key = started_at[:10]
            if day_key in daily:
                daily[day_key]["words"] += word_count
                daily[day_key]["minutes"] += round(duration_ms / 60000, 1)
                daily[day_key]["dictations"] += 1

        top_words = [
            {"word": word, "count": count}
            for word, count in sorted(word_counts.items(), key=lambda item: (-item[1], item[0]))[:12]
        ]
        return {
            "totalDictations": total_dictations,
            "totalWords": total_words,
            "totalMinutes": round(total_ms / 60000),
            "topWords": top_words,
            "last7Days": list(daily.values()),
        }

    def delete_dictation(self, dictation_id: int) -> str:
        try:
            from core.history import delete_dictation

            delete_dictation(int(dictation_id))
        except Exception:
            pass
        return self.history_snapshot_json()

    def purge_history(self) -> str:
        try:
            from core.migrations import get_connection

            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT audio_path FROM dictations WHERE audio_path IS NOT NULL AND audio_path != ''")
            audio_paths = [str(row["audio_path"]) for row in cursor.fetchall()]
            for audio_path in audio_paths:
                if audio_path and os.path.exists(audio_path):
                    try:
                        os.remove(audio_path)
                    except OSError:
                        pass
            cursor.execute("DELETE FROM dictation_contexts")
            cursor.execute("DELETE FROM dictations")
            conn.commit()
            conn.close()
        except Exception:
            pass
        return self.history_snapshot_json()

    def _modes_payload(self) -> list[dict[str, Any]]:
        try:
            from core.modes import list_auto_rules, list_modes, seed_builtins

            seed_builtins()
            rules_by_mode: dict[int, list[dict[str, Any]]] = {}
            for rule in list_auto_rules():
                mode_id = int(rule.get("mode_id") or 0)
                rules_by_mode.setdefault(mode_id, []).append(rule)
            modes = []
            for mode in list_modes(enabled_only=False):
                mode_id = int(mode.id or 0)
                modes.append(
                    {
                        "id": mode_id,
                        "name": mode.name,
                        "description": mode.description,
                        "builtin": mode.is_builtin,
                        "enabled": mode.enabled,
                        "stt": mode.stt_provider or "local",
                        "sttModel": mode.stt_model or "",
                        "format": mode.output_format or "plain",
                        "formattingPrompt": mode.formatting_prompt or "",
                        "llm": mode.llm_enabled,
                        "llmProvider": mode.llm_provider or "",
                        "llmModel": mode.llm_model or "",
                        "llmPrompt": mode.llm_prompt or "",
                        "pasteMethod": mode.paste_method or "clipboard_paste",
                        "autoSend": mode.auto_send,
                        "ctxOcr": mode.ctx_ocr,
                        "ctxSelectedText": mode.ctx_selected_text,
                        "ctxClipboard": mode.ctx_clipboard,
                        "auto": [
                            {
                                "id": int(rule.get("id") or 0),
                                "type": rule.get("match_type") or "",
                                "value": rule.get("match_value") or "",
                                "priority": int(rule.get("priority") or 0),
                                "enabled": bool(rule.get("enabled")),
                            }
                            for rule in rules_by_mode.get(mode_id, [])
                        ],
                    }
                )
            return modes
        except Exception:
            return []

    def update_mode(self, mode_id: int, patch_json: str) -> str:
        try:
            from core.modes import update_mode

            patch = json.loads(patch_json or "{}")
            mapping = {
                "name": "name",
                "description": "description",
                "enabled": "enabled",
                "format": "output_format",
                "llm": "llm_enabled",
                "llmProvider": "llm_provider",
                "llmModel": "llm_model",
                "llmPrompt": "llm_prompt",
                "stt": "stt_provider",
                "sttModel": "stt_model",
                "pasteMethod": "paste_method",
                "formattingPrompt": "formatting_prompt",
                "autoSend": "auto_send",
                "ctxOcr": "ctx_ocr",
                "ctxSelectedText": "ctx_selected_text",
                "ctxClipboard": "ctx_clipboard",
            }
            updates = {target: patch[source] for source, target in mapping.items() if source in patch}
            if "stt_provider" in updates:
                provider = str(updates.get("stt_provider") or "local")
                requested_model = str(updates.get("stt_model") or "").strip()
                if provider == "nvidia_nim_parakeet":
                    valid = {item["value"] for item in NVIDIA_NIM_MODEL_OPTIONS}
                    normalized = _nvidia_nim_model_value(requested_model)
                    updates["stt_model"] = normalized if normalized in valid else NVIDIA_NIM_DEFAULT_MODEL
                elif provider == "groq_whisper":
                    valid = {item["value"] for item in self._groq_stt_fallback_options()}
                    updates["stt_model"] = requested_model if requested_model in valid else "whisper-large-v3-turbo"
                elif provider == "local":
                    updates["stt_model"] = ""
                elif not requested_model:
                    updates["stt_model"] = ""
            elif updates.get("stt_model"):
                normalized = _nvidia_nim_model_value(str(updates["stt_model"]))
                if normalized != updates["stt_model"]:
                    updates["stt_model"] = normalized
            if updates:
                update_mode(int(mode_id), **updates)
        except Exception:
            pass
        return self.modes_snapshot_json()

    def add_mode(self, name: str) -> str:
        created_id = 0
        try:
            from core.modes import add_mode, list_modes

            base = (name or "New Mode").strip() or "New Mode"
            existing = {mode.name.lower() for mode in list_modes(enabled_only=False)}
            candidate = base
            suffix = 2
            while candidate.lower() in existing:
                candidate = f"{base} {suffix}"
                suffix += 1
            created_id = int(add_mode(candidate, description="Custom dictation mode.", output_format="plain") or 0)
        except Exception:
            pass
        try:
            payload = json.loads(self.modes_snapshot_json())
            if created_id:
                payload["createdModeId"] = created_id
            return json.dumps(payload, separators=(",", ":"))
        except Exception:
            return self.modes_snapshot_json()

    def delete_mode(self, mode_id: int) -> str:
        try:
            from core.modes import delete_mode

            delete_mode(int(mode_id))
        except Exception:
            pass
        return self.modes_snapshot_json()

    def add_auto_rule(self, mode_id: int, match_type: str, match_value: str, priority: int = 0) -> str:
        try:
            from core.modes import add_auto_rule

            match_type = (match_type or "process").strip()
            match_value = (match_value or "").strip()
            if match_value:
                add_auto_rule(int(mode_id), match_type, match_value, int(priority), True)
        except Exception:
            pass
        return self.modes_snapshot_json()

    def delete_auto_rule(self, rule_id: int) -> str:
        try:
            from core.modes import delete_auto_rule

            delete_auto_rule(int(rule_id))
        except Exception:
            pass
        return self.modes_snapshot_json()

    def _mic_level_payload(self) -> dict[str, Any]:
        with self._mic_level_lock:
            return {
                "db": round(self._mic_level_db, 1),
                "level": round(self._mic_level_value, 4),
                "error": self._mic_level_error,
            }

    def _set_mic_level(self, db: float, level: float, error: str = ""):
        with self._mic_level_lock:
            self._mic_level_db = max(-96.0, min(0.0, float(db)))
            self._mic_level_value = max(0.0, min(1.0, float(level)))
            self._mic_level_error = error

    def start_engine(self):
        if self.process and self.process.poll() is None:
            return
        self._terminate_orphan_engine_processes()
        self.settings = load_settings()
        self._loading_preview_enabled = True
        if self._shortcut_capture_active:
            self._unregister_loading_preview_shortcuts()
        else:
            self._register_loading_preview_shortcuts()
        self._engine_output_lines.clear()
        create_no_window = 0x08000000
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        model_arg = self._current_model_value()

        if getattr(sys, "frozen", False):
            if sys.platform == "darwin":
                command = [sys.executable, "--engine", f"--model={model_arg}"]
                cwd = project_root
                env = os.environ.copy()
            else:
                source_root = self._frozen_engine_source_root()
                python_exe = self._external_engine_python()
                if source_root and python_exe:
                    command = [python_exe, "-u", os.path.join(source_root, "main.py"), f"--model={model_arg}"]
                    cwd = source_root
                    env = os.environ.copy()
                    env["PYTHONPATH"] = source_root + os.pathsep + env.get("PYTHONPATH", "")
                    env.setdefault("WHISPERER_PROJECT_ROOT", source_root)
                else:
                    command = [sys.executable, "--engine", f"--model={model_arg}"]
                    cwd = project_root
                    env = os.environ.copy()
        else:
            command = [sys.executable, "-u", os.path.join(project_root, "main.py"), f"--model={model_arg}"]
            cwd = project_root
            env = os.environ.copy()

        self._log_web_ui(f"Starting engine: {' '.join(command)}")
        env["WHISPERER_UI_LOADING_PREVIEW"] = "1"
        env["WHISPERER_ENGINE_PARENT_UI"] = "1"
        if self._shortcut_capture_active:
            env["WHISPERER_HOTKEYS_PAUSED"] = "1"
        if sys.platform == "darwin":
            env["WHISPERER_ENGINE_ACCESSORY"] = "1"
            env["QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM"] = "1"
        _apply_quiet_model_env(env)
        self._apply_engine_gpu_env(env)
        try:
            log_root = os.path.join(get_app_data_dir(), "logs")
            os.makedirs(log_root, exist_ok=True)
            self._engine_ready_file = os.path.join(log_root, f"engine-ready-{time.time_ns()}.json")
            if os.path.exists(self._engine_ready_file):
                os.remove(self._engine_ready_file)
            env["WHISPERER_ENGINE_READY_FILE"] = self._engine_ready_file
        except Exception:
            self._engine_ready_file = ""

        try:
            self.process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                creationflags=create_no_window if os.name == "nt" else 0,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                close_fds=True,
            )
        except Exception as exc:
            self._engine_output_lines.append(f"Failed to start engine: {exc}")
            self._loading_preview_enabled = False
            self._hide_loading_preview()
            self._unregister_loading_preview_shortcuts()
            self._set_engine_state("stopped")
            return

        self._set_engine_state("loading")
        self._start_engine_output_reader()

    def stop_engine(self):
        self._loading_preview_enabled = False
        self._loading_preview_locked = False
        self._loading_preview_overlay.set_locked(False)
        self._loading_preview_release_timer.stop()
        self._hide_loading_preview()
        self._unregister_loading_preview_shortcuts()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        self.process = None
        self._terminate_orphan_engine_processes()
        if self._engine_ready_file:
            try:
                os.remove(self._engine_ready_file)
            except OSError:
                pass
            self._engine_ready_file = ""
        self._set_engine_state("stopped")

    def _terminate_orphan_engine_processes(self):
        if sys.platform != "darwin":
            return
        app_exe = os.path.abspath(sys.executable)
        if not app_exe.endswith("/Contents/MacOS/Whisperer"):
            return
        current_pid = os.getpid()
        try:
            output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True)
        except Exception:
            return
        pids: list[int] = []
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            pid_text, _, command = line.partition(" ")
            try:
                pid = int(pid_text)
            except ValueError:
                continue
            if pid == current_pid:
                continue
            if app_exe in command and " --engine" in command:
                pids.append(pid)
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                continue
        deadline = time.monotonic() + 2.0
        while pids and time.monotonic() < deadline:
            pids = [pid for pid in pids if self._process_exists(pid)]
            if pids:
                time.sleep(0.05)
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    @staticmethod
    def _process_exists(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def restart_engine(self):
        self.stop_engine()
        QTimer.singleShot(500, self.start_engine)

    # Tray compatibility with the legacy MainWindow.
    start_app = start_engine
    stop_app = stop_engine

    def set_paused(self, paused: bool):
        self._paused = paused
        if paused:
            self.stop_engine()
        elif self._should_auto_start_engine():
            self.start_engine()

    def show_window(self):
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if sys.platform == "darwin":
            try:
                from AppKit import NSApplication

                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            except Exception:
                pass
        QTimer.singleShot(0, self._enable_native_shadow)
        QTimer.singleShot(0, self._apply_taskbar_identity)

    def _taskbar_relaunch_exe(self) -> str | None:
        candidates = [
            os.environ.get("WHISPERER_LAUNCHER_EXE", ""),
            sys.executable if getattr(sys, "frozen", False) else "",
        ]
        project_root = os.environ.get("WHISPERER_PROJECT_ROOT", "")
        if project_root and os.path.basename(project_root).lower() == "_internal":
            candidates.append(os.path.join(os.path.dirname(project_root), "Whisperer.exe"))
        here_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if os.path.basename(here_root).lower() == "_internal":
            candidates.append(os.path.join(os.path.dirname(here_root), "Whisperer.exe"))
        candidates.append(os.path.join(here_root, "Whisperer.exe"))
        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                return os.path.abspath(candidate)
        return None

    def _apply_taskbar_identity(self):
        if os.name != "nt":
            return
        exe = self._taskbar_relaunch_exe()
        if not exe:
            return
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception:
            pass
        try:
            import pythoncom
            import win32com.propsys.propsys as propsys
            import win32com.propsys.pscon as pscon

            store = propsys.SHGetPropertyStoreForWindow(int(self.winId()), propsys.IID_IPropertyStore)
            text_type = pythoncom.VT_LPWSTR
            store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_USER_MODEL_ID, text_type))
            store.SetValue(pscon.PKEY_AppUserModel_RelaunchCommand, propsys.PROPVARIANTType(f'"{exe}"', text_type))
            store.SetValue(pscon.PKEY_AppUserModel_RelaunchIconResource, propsys.PROPVARIANTType(f"{exe},0", text_type))
            store.SetValue(pscon.PKEY_AppUserModel_RelaunchDisplayNameResource, propsys.PROPVARIANTType("Whisperer", text_type))
            store.Commit()
        except Exception:
            pass

    def _enable_native_shadow(self):
        if os.name != "nt":
            return
        try:
            hwnd = int(self.winId())
            if not hwnd:
                return
            user32 = ctypes.windll.user32
            gwl_style = -16
            ws_thickframe = 0x00040000
            ws_minimizebox = 0x00020000
            ws_maximizebox = 0x00010000
            ws_sysmenu = 0x00080000
            swp_nosize = 0x0001
            swp_nomove = 0x0002
            swp_nozorder = 0x0004
            swp_noactivate = 0x0010
            swp_framechanged = 0x0020
            get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            style = int(get_long(ctypes.c_void_p(hwnd), gwl_style))
            shadow_style = style | ws_thickframe | ws_minimizebox | ws_maximizebox | ws_sysmenu
            if shadow_style != style:
                set_long(ctypes.c_void_p(hwnd), gwl_style, shadow_style)
                user32.SetWindowPos(
                    ctypes.c_void_p(hwnd),
                    None,
                    0,
                    0,
                    0,
                    0,
                    swp_nomove | swp_nosize | swp_nozorder | swp_noactivate | swp_framechanged,
                )
            dwmapi = ctypes.windll.dwmapi
            policy = ctypes.c_int(2)  # DWMNCRP_ENABLED
            dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(2),  # DWMWA_NCRENDERING_POLICY
                ctypes.byref(policy),
                ctypes.sizeof(policy),
            )
            margins = _DwmMargins(1, 1, 1, 1)
            dwmapi.DwmExtendFrameIntoClientArea(ctypes.c_void_p(hwnd), ctypes.byref(margins))
            corner_preference = ctypes.c_int(2)  # DWMWCP_ROUND
            try:
                dwmapi.DwmSetWindowAttribute(
                    ctypes.c_void_p(hwnd),
                    ctypes.c_uint(33),  # DWMWA_WINDOW_CORNER_PREFERENCE
                    ctypes.byref(corner_preference),
                    ctypes.sizeof(corner_preference),
                )
            except Exception:
                pass
            dark = ctypes.c_int(1)
            for attr in (20, 19):  # Newer and older DWMWA_USE_IMMERSIVE_DARK_MODE values.
                try:
                    dwmapi.DwmSetWindowAttribute(
                        ctypes.c_void_p(hwnd),
                        ctypes.c_uint(attr),
                        ctypes.byref(dark),
                        ctypes.sizeof(dark),
                    )
                except Exception:
                    pass
        except Exception:
            pass

    def prepare_for_quit(self):
        self._force_quitting = True
        try:
            self.tray.hide()
        except Exception:
            pass
        self.stop_engine()

    def force_quit(self):
        self.prepare_for_quit()
        self.close()
        app = QApplication.instance()
        if app:
            app.quit()

    def _frozen_engine_source_root(self) -> str | None:
        candidates = [
            os.environ.get("WHISPERER_PROJECT_ROOT", ""),
            getattr(sys, "_MEIPASS", ""),
            os.path.join(os.path.dirname(sys.executable), "_internal"),
            os.path.dirname(sys.executable),
        ]
        for candidate in candidates:
            if candidate and os.path.exists(os.path.join(candidate, "main.py")):
                return candidate
        return None

    def _external_engine_python(self) -> str | None:
        candidates = [
            os.environ.get("WHISPERER_PYTHON"),
            sys.executable if not getattr(sys, "frozen", False) else "",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python", "Python310", "python.exe")
            if os.name == "nt"
            else "",
            shutil.which("python3"),
            shutil.which("python"),
        ]
        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                return candidate
        return None

    def _start_engine_output_reader(self):
        if not self.process or not self.process.stdout:
            return

        def _reader():
            try:
                assert self.process and self.process.stdout
                for line in self.process.stdout:
                    self._engine_output_queue.put(line.rstrip())
            except Exception as exc:
                self._engine_output_queue.put(f"ENGINE_OUTPUT_ERROR {exc}")

        threading.Thread(target=_reader, daemon=True).start()

    @staticmethod
    def _is_noisy_engine_line(line: str) -> bool:
        lowered = line.lower()
        return any(part.lower() in lowered for part in NOISY_ENGINE_LINE_PARTS)

    def _check_engine_ready_file(self) -> bool:
        if (
            self._engine_state != "loading"
            or not self.process
            or self.process.poll() is not None
            or not self._engine_ready_file
            or not os.path.exists(self._engine_ready_file)
        ):
            return False
        self._log_web_ui("Engine ready file observed")
        self._set_engine_state("running")
        return True

    def _drain_engine_output(self):
        changed = False
        while True:
            try:
                line = self._engine_output_queue.get_nowait()
            except queue.Empty:
                break
            if not line:
                continue
            if line.startswith("MIC_LEVEL "):
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        self._set_mic_level(float(parts[1]), float(parts[2]))
                    except ValueError:
                        pass
                continue
            if line.startswith("BACKUP_TRANSCRIPTION_RESULT "):
                self._handle_backup_transcription_engine_result(line)
                continue
            if line == "OPEN_UI_REQUESTED":
                self._log_web_ui("Engine requested UI window")
                self.show_window()
                continue
            if line == "STOP_ENGINE_REQUESTED":
                self._log_web_ui("Engine requested stop")
                QTimer.singleShot(0, self.stop_engine)
                return
            if self._is_noisy_engine_line(line):
                continue
            changed = True
            self._engine_output_lines.append(line)
            self._engine_output_lines = self._engine_output_lines[-200:]
            self._log_web_ui(f"Engine output: {line}")
            print(f"[engine] {line}", flush=True)
            if line == "ENGINE_READY":
                self._set_engine_state("running")
            elif line == "DICTATION_STARTED":
                self._loading_preview_release_timer.stop()
                self._loading_preview_hide_timer.stop()
                self._loading_preview_locked = False
                if self._loading_preview_overlay.isVisible():
                    self._loading_preview_overlay.set_locked(False)
                    self._loading_preview_overlay.hide_now()

        if self._check_engine_ready_file():
            changed = True

        if self.process and self.process.poll() is not None:
            return_code = self.process.returncode
            self._log_web_ui(f"Engine exited with code {return_code}")
            self.process = None
            self._set_engine_state("stopped")
            if self._backup_transcription_busy and self._backup_transcription_source == "engine":
                self._finish_last_dictation_transcription(
                    self._backup_transcription_request_id,
                    False,
                    "",
                    "The engine stopped before the last dictation could be transcribed.",
                )
            if (
                return_code == ENGINE_FORCE_STOP_RESTART_CODE
                and not self._paused
                and self.settings.get("startup", {}).get("auto_start_engine", True)
            ):
                self._set_engine_state("loading")
                QTimer.singleShot(350, self.start_engine)
                return
            changed = True

        # Engine stdout can be chatty during model startup. Avoid pushing full
        # dashboard snapshots for every line; state changes already notify React.

    def _handle_backup_transcription_engine_result(self, line: str):
        try:
            payload = json.loads(line.split(" ", 1)[1])
        except Exception as exc:
            self.backupTranscriptionFinished.emit(
                self._backup_transcription_request_id,
                False,
                "",
                f"Could not read engine transcription response: {exc}",
            )
            return
        request_id = str(payload.get("requestId") or "")
        ok = bool(payload.get("ok"))
        text = str(payload.get("text") or "")
        error = str(payload.get("error") or "")
        self.backupTranscriptionFinished.emit(request_id, ok, text, error)

    def closeEvent(self, event):
        if not self._force_quitting and self.tray and self.tray.isVisible():
            event.ignore()
            self.hide()
            return
        self.stop_engine()
        event.accept()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._enable_native_shadow)
        QTimer.singleShot(0, self._apply_taskbar_identity)
