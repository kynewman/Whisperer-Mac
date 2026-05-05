"""
Whisper transcription engine built on faster-whisper (CTranslate2).
Handles model loading, transcription with context prompts, and result formatting.
Also supports cloud STT providers.
"""

from __future__ import annotations

import json
import hashlib
import io
import logging
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave

import numpy as np

import config
from core.perf import timed
from core.settings import load_settings


_model = None
_warmed_up = False
_MODEL_LOAD_LOCK = threading.RLock()
_PARAKEET_WARMUP_TRANSCRIBE = os.environ.get("WHISPERER_PARAKEET_WARMUP_TRANSCRIBE") == "1"
DEFAULT_OPENAI_STT_MODEL = "gpt-4o-transcribe"
DEFAULT_GROQ_STT_MODEL = "whisper-large-v3-turbo"
DEFAULT_DEEPGRAM_STT_MODEL = "nova-3"
DEFAULT_NVIDIA_NIM_STT_MODEL = "parakeet-tdt-0.6b-v2"
NVIDIA_RIVA_STREAMING_MODEL = "parakeet-1.1b-rnnt-multilingual-asr"
NVIDIA_HOSTED_RIVA_URI = "grpc.nvcf.nvidia.com:443"
NVIDIA_NIM_LOCAL_HTTP_URL = "http://localhost:9000/v1/audio/transcriptions"
NVIDIA_RIVA_FUNCTION_IDS = {
    "parakeet-tdt-0.6b-v2": "d3fe9151-442b-4204-a70d-5fcc597fd610",
    "parakeet-ctc-0.6b-asr": "d8dd4e9b-fbf5-4fb0-9dba-8cf436c8d965",
    "parakeet-1.1b-rnnt-multilingual-asr": "71203149-d3b7-4460-8231-1be2543a1fca",
    "parakeet-1.1b-rnnt-multilingual": "71203149-d3b7-4460-8231-1be2543a1fca",
}
API_USER_AGENT = "Whisperer/6.0.1"
_RIVA_CLIENT_CACHE: dict[tuple[str, str, str, str, str], tuple[object, object]] = {}
_RIVA_CLIENT_LOCK = threading.Lock()


def _quiet_model_telemetry_logs() -> None:
    """Silence training telemetry warnings from inference-only model loading."""
    os.environ.setdefault("RANK", "0")
    os.environ.setdefault("LOCAL_RANK", "0")
    os.environ.setdefault("WORLD_SIZE", "1")
    os.environ.setdefault("NEMO_LOGGING_LEVEL", "ERROR")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    for name in ("nv_one_logger", "nemo.lightning.one_logger_callback"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.ERROR)
        logger.addHandler(logging.NullHandler())


def _is_parakeet_model(model_name: str | None = None) -> bool:
    return (model_name or config.WHISPER_MODEL_SIZE).lower().startswith("nvidia/parakeet")


def _cache_dir_name(model_name: str) -> str:
    return "models--" + model_name.replace("/", "--")


def _model_cache_exists(model_name: str) -> bool:
    """Best-effort cache detection for Hugging Face model snapshots."""
    candidates = [
        os.path.join(config.MODEL_CACHE_DIR, _cache_dir_name(model_name)),
        os.path.join(config.MODEL_CACHE_DIR, "huggingface", "hub", _cache_dir_name(model_name)),
    ]
    for root in candidates:
        snapshots = os.path.join(root, "snapshots")
        if not os.path.isdir(snapshots):
            continue
        for snapshot in os.listdir(snapshots):
            path = os.path.join(snapshots, snapshot)
            if os.path.isdir(path) and any(os.scandir(path)):
                return True
    return False


def _configure_model_cache(model_name: str) -> bool:
    """Point model libraries at the app model cache and enable offline mode when safe."""
    _quiet_model_telemetry_logs()
    os.environ.setdefault("HF_HOME", os.path.join(config.MODEL_CACHE_DIR, "huggingface"))
    os.environ.setdefault("NEMO_HOME", os.path.join(config.MODEL_CACHE_DIR, "nemo"))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    cached = _model_cache_exists(model_name)
    if cached:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    return cached


def trim_silence(audio: np.ndarray, threshold: float = 0.003, pad_ms: int = 220) -> np.ndarray:
    """Trim leading/trailing silence while preserving a little natural padding."""
    if audio is None or len(audio) == 0:
        return np.zeros(0, dtype=np.float32)

    data = audio.astype(np.float32, copy=False)
    sample_rate = int(config.AUDIO_SAMPLE_RATE)
    frame = max(1, int(sample_rate * 0.02))
    usable = (len(data) // frame) * frame
    if usable <= 0:
        return data

    frames = data[:usable].reshape(-1, frame)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    active = np.flatnonzero(rms > threshold)
    if active.size == 0:
        return data

    pad = int(sample_rate * pad_ms / 1000)
    start = max(0, int(active[0]) * frame - pad)
    end = min(len(data), (int(active[-1]) + 1) * frame + pad)
    if start == 0 and end == len(data):
        return data
    return data[start:end].astype(np.float32, copy=False)


def _silence_trim_enabled() -> bool:
    try:
        return bool(load_settings().get("performance", {}).get("silence_trim_enabled", True))
    except Exception:
        return True


def load_model():
    """Load the selected local STT model once."""
    global _model
    if _model is None:
        with _MODEL_LOAD_LOCK:
            if _model is None:
                with timed("model_load"):
                    if _is_parakeet_model():
                        _configure_model_cache(config.WHISPER_MODEL_SIZE)
                        try:
                            import torch
                            if hasattr(torch, "set_float32_matmul_precision"):
                                torch.set_float32_matmul_precision("high")
                            if torch.cuda.is_available():
                                torch.backends.cudnn.benchmark = True
                        except Exception:
                            pass
                        try:
                            with timed("import_nemo_asr"):
                                import nemo.collections.asr as nemo_asr
                        except Exception as exc:
                            raise RuntimeError(
                                "NVIDIA Parakeet requires NeMo ASR. Install it with "
                                "\"pip install 'nemo_toolkit[asr]'\" in the Whisperer Python environment."
                            ) from exc
                        _model = nemo_asr.models.ASRModel.from_pretrained(model_name=config.WHISPER_MODEL_SIZE)
                        try:
                            from omegaconf import OmegaConf
                            OmegaConf.set_struct(_model.cfg, False)
                        except Exception:
                            pass
                        if getattr(_model.cfg, "validation_ds", None) is None:
                            _model.cfg.validation_ds = {}
                        if getattr(_model.cfg, "test_ds", None) is None:
                            _model.cfg.test_ds = {}
                        if hasattr(_model, "to"):
                            _model = _model.to(config.WHISPER_DEVICE)
                        if hasattr(_model, "eval"):
                            _model.eval()
                    else:
                        cached = _configure_model_cache(config.WHISPER_MODEL_SIZE)
                        with timed("import_faster_whisper"):
                            from faster_whisper import WhisperModel
                        kwargs = {
                            "device": config.WHISPER_DEVICE,
                            "compute_type": config.WHISPER_COMPUTE_TYPE,
                            "download_root": config.MODEL_CACHE_DIR,
                        }
                        if cached:
                            kwargs["local_files_only"] = True
                        try:
                            _model = WhisperModel(config.WHISPER_MODEL_SIZE, **kwargs)
                        except TypeError:
                            kwargs.pop("local_files_only", None)
                            _model = WhisperModel(config.WHISPER_MODEL_SIZE, **kwargs)
    return _model


def warmup_model() -> None:
    """Run one tiny transcription so CT2/model first-use work happens at startup."""
    global _warmed_up
    if _warmed_up:
        return
    with _MODEL_LOAD_LOCK:
        if _warmed_up:
            return

        model = load_model()
        if _is_parakeet_model() and not _PARAKEET_WARMUP_TRANSCRIBE:
            with timed("model_warmup"):
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.synchronize()
                except Exception:
                    pass
            _warmed_up = True
            return

        silence = np.zeros(config.AUDIO_SAMPLE_RATE // 4, dtype=np.float32)
        with timed("model_warmup"):
            if _is_parakeet_model():
                _transcribe_parakeet_audio(silence)
            else:
                segments, _info = model.transcribe(
                    silence,
                    language=config.WHISPER_LANGUAGE,
                    beam_size=1,
                    condition_on_previous_text=False,
                    vad_filter=True,
                    vad_parameters=dict(
                        min_silence_duration_ms=300,
                        speech_pad_ms=100,
                    ),
                )
                for _segment in segments:
                    pass
        _warmed_up = True


# ---------------------------------------------------------------------------
# Cloud STT providers
# ---------------------------------------------------------------------------

def _audio_to_wav_bytes(audio: np.ndarray) -> bytes:
    """Convert float32 numpy array to WAV bytes."""
    buf = io.BytesIO()
    scaled = (audio * 32767).astype(np.int16)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(scaled.tobytes())
    return buf.getvalue()


def _audio_to_pcm16_bytes(audio: np.ndarray) -> bytes:
    """Convert float32 mono audio into raw 16-bit PCM for Riva gRPC."""
    clipped = np.clip(audio.astype(np.float32, copy=False), -1.0, 1.0)
    return (clipped * 32767).astype(np.int16).tobytes()


def _parakeet_text(result) -> str:
    item = result[0] if isinstance(result, (list, tuple)) and result else result
    if hasattr(item, "text"):
        return str(item.text or "")
    if isinstance(item, dict):
        return str(item.get("text") or item.get("pred_text") or "")
    return str(item or "")


def _transcribe_parakeet_audio(audio: np.ndarray) -> str:
    model = load_model()
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            tmp.write(_audio_to_wav_bytes(audio))
            tmp_path = tmp.name
        with timed("transcribe_parakeet"):
            try:
                result = model.transcribe([tmp_path], batch_size=1)
            except TypeError:
                result = model.transcribe([tmp_path])
        return _parakeet_text(result).strip()
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _json_text_payload(payload: str) -> str:
    result = json.loads(payload or "{}")
    return str(result.get("text") or "").strip()


def _json_text_response(resp) -> str:
    return _json_text_payload(resp.read().decode("utf-8"))


def _multipart_transcription_body(
    audio: np.ndarray,
    *,
    model: str | None,
    language: str = "en",
    prompt: str | None = None,
    include_model: bool = True,
) -> tuple[bytes, str]:
    boundary = "----PythonFormBoundary"
    wav_bytes = _audio_to_wav_bytes(audio)
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode("utf-8")
    body += wav_bytes
    if include_model and model:
        body += f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'.encode("utf-8")
    else:
        body += b"\r\n"
    if language:
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="language"\r\n\r\n{language}\r\n'.encode("utf-8")
    if prompt:
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="prompt"\r\n\r\n{prompt}\r\n'.encode("utf-8")
    body += f"--{boundary}--\r\n".encode("utf-8")
    return body, boundary


def _normalize_transcriptions_url(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return "http://localhost:8000/v1/audio/transcriptions"
    if url.endswith("/audio/transcriptions"):
        return url
    if url.endswith("/v1"):
        return f"{url}/audio/transcriptions"
    return f"{url}/v1/audio/transcriptions"


def _find_curl() -> str | None:
    path = shutil.which("curl")
    if path:
        return path
    for candidate in ("/usr/bin/curl", "/opt/homebrew/bin/curl", "/usr/local/bin/curl"):
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def transcribe_openai(
    audio: np.ndarray,
    api_key: str,
    language: str = "en",
    prompt: str | None = None,
    model: str | None = None,
) -> str:
    """Transcribe via OpenAI's Speech-to-Text API."""
    model = (model or DEFAULT_OPENAI_STT_MODEL).strip() or DEFAULT_OPENAI_STT_MODEL
    prompt_value = None if model == "gpt-4o-transcribe-diarize" else prompt
    body, boundary = _multipart_transcription_body(
        audio,
        model=model,
        language=language,
        prompt=prompt_value,
    )

    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": API_USER_AGENT,
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return _json_text_response(resp)


def transcribe_openai_compatible(
    audio: np.ndarray,
    api_key: str | None,
    base_url: str,
    language: str = "en",
    prompt: str | None = None,
    model: str | None = None,
) -> str:
    """Transcribe through an OpenAI-compatible `/v1/audio/transcriptions` endpoint."""
    model = (model or "whisper-large-v3").strip() or "whisper-large-v3"
    body, boundary = _multipart_transcription_body(audio, model=model, language=language, prompt=prompt)
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        _normalize_transcriptions_url(base_url),
        data=body,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return _json_text_response(resp)


def transcribe_groq(
    audio: np.ndarray,
    api_key: str,
    language: str = "en",
    prompt: str | None = None,
    model: str | None = None,
) -> str:
    """Transcribe via Groq Whisper endpoint."""
    model = (model or DEFAULT_GROQ_STT_MODEL).strip() or DEFAULT_GROQ_STT_MODEL
    curl_path = _find_curl()
    if curl_path:
        return _transcribe_groq_with_curl(
            audio,
            api_key=api_key,
            language=language,
            prompt=prompt,
            model=model,
            curl_path=curl_path,
        )

    body, boundary = _multipart_transcription_body(audio, model=model, language=language, prompt=prompt)

    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": API_USER_AGENT,
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return _json_text_response(resp)


def _transcribe_groq_with_curl(
    audio: np.ndarray,
    *,
    api_key: str,
    language: str = "en",
    prompt: str | None = None,
    model: str,
    curl_path: str,
) -> str:
    """Use curl for Groq uploads; Cloudflare can reject Python urllib multipart posts."""
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            tmp.write(_audio_to_wav_bytes(audio))
            tmp_path = tmp.name

        args = [
            curl_path,
            "--silent",
            "--show-error",
            "--max-time",
            "30",
            "https://api.groq.com/openai/v1/audio/transcriptions",
            "--header",
            f"Authorization: Bearer {api_key}",
            "--header",
            f"User-Agent: {API_USER_AGENT}",
            "--header",
            "Accept: application/json",
            "--form",
            f"file=@{tmp_path};type=audio/wav",
            "--form-string",
            f"model={model}",
            "--form-string",
            "response_format=json",
            "--write-out",
            "\n%{http_code}",
        ]
        if language:
            args.extend(["--form-string", f"language={language}"])
        if prompt:
            args.extend(["--form-string", f"prompt={prompt}"])

        proc = subprocess.run(args, capture_output=True, text=True, timeout=35)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise RuntimeError(f"Groq request failed: {detail}")

        body, _sep, status_text = (proc.stdout or "").rpartition("\n")
        try:
            status = int(status_text.strip())
        except ValueError:
            status = 0
        if not 200 <= status < 300:
            detail = (body or proc.stderr or "").strip()[:500]
            raise RuntimeError(f"Groq rejected the request ({status}): {detail}")
        return _json_text_payload(body)
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def transcribe_deepgram(audio: np.ndarray, api_key: str, language: str = "en", model: str | None = None) -> str:
    """Transcribe via Deepgram's prerecorded Speech-to-Text API."""
    wav_bytes = _audio_to_wav_bytes(audio)
    query = urllib.parse.urlencode(
        {
            "model": (model or DEFAULT_DEEPGRAM_STT_MODEL).strip() or DEFAULT_DEEPGRAM_STT_MODEL,
            "language": language or "en",
            "smart_format": "true",
        }
    )
    url = f"https://api.deepgram.com/v1/listen?{query}"
    req = urllib.request.Request(
        url,
        data=wav_bytes,
        headers={
            "Authorization": f"Token {api_key}",
            "Content-Type": "audio/wav",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        return result["results"]["channels"][0]["alternatives"][0].get("transcript", "")


def _nvidia_model_id(model: str | None) -> str:
    cleaned = (model or DEFAULT_NVIDIA_NIM_STT_MODEL).strip() or DEFAULT_NVIDIA_NIM_STT_MODEL
    aliases = {
        "parakeet-1.1b-rnnt-multilingual": "parakeet-1.1b-rnnt-multilingual-asr",
    }
    return aliases.get(cleaned, cleaned)


def nvidia_riva_model_supports_streaming(model: str | None) -> bool:
    """NVIDIA hosted Parakeet streams through the RNNT Riva endpoint."""
    return _nvidia_model_id(model) in NVIDIA_RIVA_FUNCTION_IDS


def nvidia_riva_streaming_model(model: str | None) -> str:
    """Return the hosted Riva model to use for low-latency Parakeet streaming."""
    model_id = _nvidia_model_id(model)
    if "rnnt" in model_id.lower():
        return model_id
    return NVIDIA_RIVA_STREAMING_MODEL


def _nvidia_hosted_uri(base_url: str | None) -> str:
    raw = (base_url or "").strip()
    if not raw:
        return NVIDIA_HOSTED_RIVA_URI
    parsed = urllib.parse.urlparse(raw)
    local_hosts = {"localhost:9000", "127.0.0.1:9000"}
    if parsed.netloc.lower() in local_hosts:
        return NVIDIA_HOSTED_RIVA_URI
    if raw.lower().rstrip("/") == NVIDIA_NIM_LOCAL_HTTP_URL.lower().rstrip("/"):
        return NVIDIA_HOSTED_RIVA_URI
    if parsed.scheme in {"grpc", "grpcs", "http", "https"}:
        raw = parsed.netloc or parsed.path
    return raw.strip().strip("/") or NVIDIA_HOSTED_RIVA_URI


def _should_use_nvidia_hosted_api(api_key: str | None, base_url: str | None) -> bool:
    raw = (base_url or "").strip()
    lower = raw.lower().rstrip("/")
    if lower.startswith(("grpc://", "grpcs://")) or "grpc.nvcf.nvidia.com" in lower:
        return True
    if not api_key:
        return False
    return lower in {
        "",
        NVIDIA_NIM_LOCAL_HTTP_URL.lower().rstrip("/"),
        "http://localhost:9000/v1/audio/transcriptions",
        "http://127.0.0.1:9000/v1/audio/transcriptions",
    }


def _nvidia_language_code(language: str | None, model: str) -> str:
    value = (language or "").strip()
    if not value or value.lower() in {"auto", "multi"}:
        return "en-US" if "multilingual" not in model else ""
    if value.lower() == "en":
        return "en-US"
    return value


def _riva_response_text(response) -> str:
    parts: list[str] = []
    for result in getattr(response, "results", []) or []:
        alternatives = getattr(result, "alternatives", []) or []
        if not alternatives:
            continue
        text = str(getattr(alternatives[0], "transcript", "") or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts).strip()


def _riva_result_text(result) -> str:
    alternatives = getattr(result, "alternatives", []) or []
    if not alternatives:
        return ""
    return str(getattr(alternatives[0], "transcript", "") or "").strip()


def _riva_cache_key(api_key: str | None, base_url: str | None, language: str | None, model_id: str) -> tuple[str, str, str, str, str]:
    key_hash = hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()[:16]
    return (
        _nvidia_hosted_uri(base_url),
        NVIDIA_RIVA_FUNCTION_IDS.get(model_id, ""),
        _nvidia_language_code(language, model_id),
        model_id,
        key_hash,
    )


def _get_riva_service_and_config(
    api_key: str | None,
    base_url: str | None,
    language: str = "en",
    model: str | None = None,
) -> tuple[object, object]:
    if not api_key:
        raise RuntimeError("NVIDIA API key is required for the hosted Parakeet API.")

    model_id = _nvidia_model_id(model)
    function_id = NVIDIA_RIVA_FUNCTION_IDS.get(model_id)
    if not function_id:
        raise RuntimeError(f"Unsupported NVIDIA Parakeet API model: {model_id}")

    cache_key = _riva_cache_key(api_key, base_url, language, model_id)
    with _RIVA_CLIENT_LOCK:
        cached = _RIVA_CLIENT_CACHE.get(cache_key)
        if cached is not None:
            return cached

    try:
        import riva.client
        from riva.client.proto import riva_audio_pb2 as raudio
    except Exception as exc:
        raise RuntimeError(
            "NVIDIA Parakeet API support requires nvidia-riva-client in the Whisperer environment."
        ) from exc

    auth = riva.client.Auth(
        use_ssl=True,
        uri=_nvidia_hosted_uri(base_url),
        metadata_args=[
            ["function-id", function_id],
            ["authorization", f"Bearer {api_key}"],
        ],
        options=[
            ("grpc.max_send_message_length", 50 * 1024 * 1024),
            ("grpc.max_receive_message_length", 50 * 1024 * 1024),
        ],
    )
    recognition_config = riva.client.RecognitionConfig(
        encoding=raudio.LINEAR_PCM,
        sample_rate_hertz=16000,
        language_code=_nvidia_language_code(language, model_id),
        max_alternatives=1,
        audio_channel_count=1,
        enable_automatic_punctuation=True,
    )
    service = riva.client.ASRService(auth)
    value = (service, recognition_config)
    with _RIVA_CLIENT_LOCK:
        _RIVA_CLIENT_CACHE[cache_key] = value
    return value


def prewarm_nvidia_riva(
    api_key: str | None,
    base_url: str | None = None,
    language: str = "en",
    models: list[str] | tuple[str, ...] | set[str] | None = None,
) -> int:
    """Import/cache hosted NVIDIA Riva clients before the first dictation needs them."""
    if not api_key:
        return 0
    warmed = 0
    for model in models or (DEFAULT_NVIDIA_NIM_STT_MODEL,):
        _get_riva_service_and_config(api_key, base_url, language=language, model=model)
        warmed += 1
    return warmed


class NvidiaStreamingTranscriber:
    """Low-latency NVIDIA Riva streaming ASR session for push-to-talk dictation."""

    def __init__(
        self,
        api_key: str | None,
        base_url: str | None = None,
        language: str = "en",
        model: str | None = None,
        text_callback=None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.language = language
        self.model = model
        self.text_callback = text_callback
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=128)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._final_parts: list[str] = []
        self._partial_text = ""
        self._best_text = ""
        self._error = ""
        self._started_at = 0.0
        self._last_text_at = 0.0
        self._final_count = 0

    def start(self) -> bool:
        if self._thread is not None:
            return True
        self._stop_event.clear()
        self._started_at = time.perf_counter()
        with self._lock:
            self._final_parts.clear()
            self._partial_text = ""
            self._best_text = ""
            self._error = ""
            self._last_text_at = 0.0
            self._final_count = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def feed_audio(self, float_array: np.ndarray) -> None:
        if self._stop_event.is_set() or float_array is None or len(float_array) == 0:
            return
        try:
            self._queue.put_nowait(float_array.astype(np.float32, copy=True))
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(float_array.astype(np.float32, copy=True))
            except queue.Full:
                pass

    def finish(self, timeout_s: float = 0.75) -> str:
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, timeout_s))
        with self._lock:
            return self._best_text.strip()

    @property
    def error(self) -> str:
        with self._lock:
            return self._error

    def text_state(self) -> dict[str, object]:
        with self._lock:
            return {
                "text": self._best_text.strip(),
                "partial": self._partial_text.strip(),
                "final_count": self._final_count,
                "last_text_at": self._last_text_at,
                "error": self._error,
            }

    def adaptive_finalize_timeout(self, max_wait_s: float, fast_wait_s: float = 0.12) -> float:
        state = self.text_state()
        text = str(state.get("text") or "")
        last_text_at = float(state.get("last_text_at") or 0.0)
        stable_for = time.perf_counter() - last_text_at if last_text_at else 0.0
        if not text:
            return min(max_wait_s, 0.40)
        safe_fast_wait = max(0.20, fast_wait_s)
        if stable_for < 0.12:
            return min(max_wait_s, max(0.24, safe_fast_wait))
        if int(state.get("final_count") or 0) > 0:
            return min(max_wait_s, safe_fast_wait)
        if len(text) >= 4 and stable_for >= 0.22:
            return min(max_wait_s, safe_fast_wait)
        return min(max_wait_s, 0.32)

    def _audio_chunks(self):
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                item = self._queue.get(timeout=0.08)
            except queue.Empty:
                continue
            if item is None:
                break
            yield _audio_to_pcm16_bytes(item)

    def _run(self) -> None:
        try:
            import riva.client

            service, recognition_config = _get_riva_service_and_config(
                self.api_key,
                self.base_url,
                language=self.language,
                model=self.model,
            )
            streaming_config = riva.client.StreamingRecognitionConfig(
                config=recognition_config,
                interim_results=True,
            )
            for response in service.streaming_response_generator(self._audio_chunks(), streaming_config):
                for result in getattr(response, "results", []) or []:
                    text = _riva_result_text(result)
                    if not text:
                        continue
                    with self._lock:
                        if bool(getattr(result, "is_final", False)):
                            self._final_parts.append(text)
                            self._partial_text = ""
                            self._final_count += 1
                        else:
                            self._partial_text = text
                        self._best_text = " ".join([*self._final_parts, self._partial_text]).strip()
                        self._last_text_at = time.perf_counter()
                        current = self._best_text
                    if current and self.text_callback:
                        self.text_callback(current)
        except Exception as exc:
            with self._lock:
                self._error = str(exc)
        finally:
            elapsed_ms = (time.perf_counter() - self._started_at) * 1000.0 if self._started_at else 0.0
            try:
                from core.perf import record_timing

                record_timing("transcribe_nvidia_streaming_session", elapsed_ms)
            except Exception:
                pass


def _transcribe_nvidia_riva_hosted(
    audio: np.ndarray,
    api_key: str | None,
    base_url: str | None = None,
    language: str = "en",
    model: str | None = None,
) -> str:
    service, recognition_config = _get_riva_service_and_config(api_key, base_url, language=language, model=model)
    with timed("transcribe_nvidia_riva"):
        response = service.offline_recognize(_audio_to_pcm16_bytes(audio), recognition_config)
    return _riva_response_text(response)


def transcribe_nvidia_nim(
    audio: np.ndarray,
    api_key: str | None,
    base_url: str | None = None,
    language: str = "en",
    model: str | None = None,
) -> str:
    """Transcribe via NVIDIA's hosted Parakeet API or a local NIM HTTP endpoint."""
    if _should_use_nvidia_hosted_api(api_key, base_url):
        return _transcribe_nvidia_riva_hosted(
            audio,
            api_key,
            base_url=base_url,
            language=language,
            model=model,
        )

    language_value = (language or "").strip() or "multi"
    if language_value.lower() == "auto":
        language_value = "multi"
    body, boundary = _multipart_transcription_body(
        audio,
        model=_nvidia_model_id(model),
        language=language_value,
        include_model=False,
    )
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        _normalize_transcriptions_url(base_url or NVIDIA_NIM_LOCAL_HTTP_URL),
        data=body,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return _json_text_response(resp)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def transcribe_cloud(
    audio: np.ndarray,
    provider: str,
    api_key: str | None,
    language: str = "en",
    prompt: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> str:
    with timed("trim_silence"):
        audio = trim_silence(audio) if _silence_trim_enabled() else audio
    if provider == "openai_whisper":
        return transcribe_openai(audio, api_key or "", language=language, prompt=prompt, model=model)
    if provider == "groq_whisper":
        return transcribe_groq(audio, api_key or "", language=language, prompt=prompt, model=model)
    if provider == "deepgram":
        return transcribe_deepgram(audio, api_key or "", language=language, model=model)
    if provider == "openai_compatible_stt":
        return transcribe_openai_compatible(
            audio,
            api_key,
            base_url or "http://localhost:8000/v1/audio/transcriptions",
            language=language,
            prompt=prompt,
            model=model,
        )
    if provider == "nvidia_nim_parakeet":
        return transcribe_nvidia_nim(audio, api_key, base_url=base_url, language=language, model=model)
    raise ValueError(f"Unknown cloud STT provider: {provider}")


# ---------------------------------------------------------------------------
# Local
# ---------------------------------------------------------------------------

def transcribe(audio_ndarray, context_words: str = "", selected_text: str = "", clipboard_context: str = "", ui_automation_text: str = "") -> str:
    """
    Transcribe a numpy float32 audio array (16 kHz mono).
    """
    model = load_model()
    with timed("trim_silence"):
        trimmed_audio = trim_silence(audio_ndarray) if _silence_trim_enabled() else audio_ndarray
    if _is_parakeet_model():
        return _transcribe_parakeet_audio(trimmed_audio)

    prompt_parts: list[str] = []
    if context_words:
        prompt_parts.append(f"Screen context:\n{context_words}")
    if ui_automation_text:
        prompt_parts.append(f"Focused control:\n{ui_automation_text}")
    if clipboard_context:
        prompt_parts.append(f"Recent clipboard:\n{clipboard_context}")
    if selected_text:
        prompt_parts.append(f"Selected text:\n{selected_text}")

    initial_prompt = "\n\n".join(prompt_parts) if prompt_parts else None

    with timed("transcribe"):
        segments, _info = model.transcribe(
            trimmed_audio,
            language=config.WHISPER_LANGUAGE,
            beam_size=config.WHISPER_BEAM_SIZE,
            initial_prompt=initial_prompt,
            condition_on_previous_text=False,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=500,
                speech_pad_ms=300,
            ),
        )

        return " ".join(segment.text.strip() for segment in segments)
