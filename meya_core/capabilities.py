"""Recognizer capability negotiation independent of model brand names."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StreamingMode(str, Enum):
    NONE = "none"
    WINDOWED = "windowed"
    NATIVE = "native"


class HotwordMode(str, Enum):
    NONE = "none"
    PROMPT = "prompt"
    NATIVE = "native"
    ACOUSTIC = "acoustic"


@dataclass(frozen=True)
class RecognizerCapabilities:
    backend: str
    model: str
    role: str
    streaming: StreamingMode
    hotwords: HotwordMode
    punctuation: bool
    languages: tuple[str, ...]
    protocol_versions: tuple[int, ...] = (2,)
    audio_transports: tuple[str, ...] = ("framed_pcm16",)

    def as_dict(self) -> dict:
        return {
            "backend": self.backend,
            "model": self.model,
            "role": self.role,
            "streaming_mode": self.streaming.value,
            "hotword_mode": self.hotwords.value,
            "punctuation": self.punctuation,
            "languages": list(self.languages),
            "protocol_versions": list(self.protocol_versions),
            "audio_transports": list(self.audio_transports),
        }
