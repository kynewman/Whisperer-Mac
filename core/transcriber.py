"""
Whisper transcription engine built on faster-whisper (CTranslate2).
Handles model loading, transcription with context prompts, and result formatting.
Also supports cloud STT providers.
"""

from __future__ import annotations

import json
import io
import logging
import os
import shutil
import subprocess
import tempfile
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
_PARAKEET_WARMUP_TRANSCRIBE = os.environ.get("WHISPERER_PARAKEET_WARMUP_TRANSCRIBE") == "1"
DEFAULT_OPENAI_STT_MODEL = "gpt-4o-transcribe"
DEFAULT_GROQ_STT_MODEL = "whisper-large-v3-turbo"
DEFAULT_DEEPGRAM_STT_MODEL = "nova-3"
DEFAULT_NVIDIA_NIM_STT_MODEL = "parakeet-1.1b-rnnt-multilingual"
API_USER_AGENT = "Whisperer/5.5.2"


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


def transcribe_nvidia_nim(
    audio: np.ndarray,
    api_key: str | None,
    base_url: str | None = None,
    language: str = "en",
    model: str | None = None,
) -> str:
    """Transcribe via NVIDIA ASR NIM HTTP endpoint."""
    language_value = (language or "").strip() or "multi"
    if language_value.lower() == "auto":
        language_value = "multi"
    body, boundary = _multipart_transcription_body(
        audio,
        model=(model or DEFAULT_NVIDIA_NIM_STT_MODEL).strip() or DEFAULT_NVIDIA_NIM_STT_MODEL,
        language=language_value,
        include_model=False,
    )
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        _normalize_transcriptions_url(base_url or "http://localhost:9000/v1/audio/transcriptions"),
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
