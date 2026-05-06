"""LLM provider registry and post-processing pipeline (local + cloud)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Dict

from core.providers import LLMProvider


log = logging.getLogger("whisperer.llm")
API_USER_AGENT = "Whisperer/6.0.3"


BUILTIN_PROMPT_TEMPLATES: Dict[str, str] = {
    "email": "Rewrite the following dictated text as a polished professional email body. Preserve the meaning exactly. Output only the rewritten text.\n\n{text}",
    "note": "Clean up the following dictated text into clear, well-punctuated prose. Output only the cleaned text.\n\n{text}",
    "coding": "Reformat the following dictated text as a code comment or docstring. Output only the result.\n\n{text}",
    "meeting": "Rewrite the following dictated notes as structured meeting notes with bullet points. Output only the result.\n\n{text}",
    "message": "Clean up the following dictated message for sending in a chat app. Keep it conversational and concise. Output only the cleaned text.\n\n{text}",
    "plain": "{text}",
}


# ---------------------------------------------------------------------------
# Generic request helper
# ---------------------------------------------------------------------------

def _post_json(url: str, payload: dict, headers: dict, timeout_s: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": API_USER_AGENT, "Accept": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Local providers
# ---------------------------------------------------------------------------

class LocalOllamaProvider:
    """Ollama /api/generate endpoint."""

    def __init__(self, model: str, base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url.rstrip("/")

    def complete(self, prompt: str, timeout_s: int = 10) -> str:
        url = f"{self.base_url}/api/generate"
        result = _post_json(url, {"model": self.model, "prompt": prompt, "stream": False}, {}, timeout_s)
        return result.get("response", "")


class LocalOpenAICompatProvider:
    """OpenAI-compatible local endpoint (e.g. llama.cpp server, LM Studio, vLLM)."""

    def __init__(self, model: str, base_url: str, api_key: str | None = None):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def complete(self, prompt: str, timeout_s: int = 10) -> str:
        url = f"{self.base_url}/v1/chat/completions"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a helpful assistant that rewrites dictated text."},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }
        result = _post_json(url, payload, headers, timeout_s)
        return result["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Cloud providers
# ---------------------------------------------------------------------------

class OpenAIProvider:
    """OpenAI GPT via official API."""

    def __init__(self, model: str = "gpt-4o-mini", api_key: str = ""):
        self.model = model
        self.api_key = api_key

    def complete(self, prompt: str, timeout_s: int = 10) -> str:
        result = _post_json(
            "https://api.openai.com/v1/chat/completions",
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant that rewrites dictated text."},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
            },
            {"Authorization": f"Bearer {self.api_key}"},
            timeout_s,
        )
        return result["choices"][0]["message"]["content"]


class AnthropicProvider:
    """Anthropic Claude via official API."""

    def __init__(self, model: str = "claude-3-haiku-20240307", api_key: str = ""):
        self.model = model
        self.api_key = api_key

    def complete(self, prompt: str, timeout_s: int = 10) -> str:
        result = _post_json(
            "https://api.anthropic.com/v1/messages",
            {
                "model": self.model,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            },
            {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout_s,
        )
        return result["content"][0]["text"]


class GroqProvider:
    """Groq via OpenAI-compatible endpoint."""

    def __init__(self, model: str = "llama3-8b-8192", api_key: str = ""):
        self.model = model
        self.api_key = api_key

    def complete(self, prompt: str, timeout_s: int = 10) -> str:
        result = _post_json(
            "https://api.groq.com/openai/v1/chat/completions",
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant that rewrites dictated text."},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
            },
            {"Authorization": f"Bearer {self.api_key}"},
            timeout_s,
        )
        return result["choices"][0]["message"]["content"]


def get_provider(provider_name: str, model: str, base_url: str = "", api_key: str | None = None) -> LLMProvider:
    """Return a concrete LLMProvider by name."""
    if provider_name == "ollama":
        return LocalOllamaProvider(model, base_url=base_url or "http://localhost:11434")
    if provider_name in ("openai_compat", "openai-compatible"):
        return LocalOpenAICompatProvider(model, base_url=base_url or "http://localhost:8000", api_key=api_key)
    if provider_name == "openai":
        return OpenAIProvider(model=model or "gpt-4o-mini", api_key=api_key or "")
    if provider_name == "anthropic":
        return AnthropicProvider(model=model or "claude-3-haiku-20240307", api_key=api_key or "")
    if provider_name == "groq":
        return GroqProvider(model=model or "llama3-8b-8192", api_key=api_key or "")
    raise ValueError(f"Unknown LLM provider: {provider_name}")


def process(
    text: str,
    prompt_template: str = "{text}",
    provider_name: str = "ollama",
    model: str = "llama3.1",
    timeout_s: int = 10,
    base_url: str = "",
    api_key: str | None = None,
) -> str:
    """
    Send text through an LLM using the given prompt template.

    Returns the LLM output on success, or the original text on failure/timeout.
    """
    if not text.strip():
        return text
    if not prompt_template.strip():
        prompt_template = "{text}"
    if "{text}" not in prompt_template:
        prompt_template = prompt_template + "\n\n{text}"

    prompt = prompt_template.format(text=text)

    try:
        provider = get_provider(provider_name, model, base_url=base_url, api_key=api_key)
        result = provider.complete(prompt, timeout_s=timeout_s)
        return result.strip() if result.strip() else text
    except urllib.error.URLError as exc:
        log.warning("LLM connection failed (%s): %s", provider_name, exc)
        return text
    except Exception as exc:
        log.warning("LLM processing failed (%s): %s", provider_name, exc)
        return text
