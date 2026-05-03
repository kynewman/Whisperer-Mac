"""Provider protocols for STT and LLM backends."""

from __future__ import annotations

from typing import Protocol


class STTProvider(Protocol):
    def transcribe(self, audio_ndarray, language: str, prompt: str | None) -> str: ...


class LLMProvider(Protocol):
    def complete(self, prompt: str, timeout_s: int = 10) -> str: ...
