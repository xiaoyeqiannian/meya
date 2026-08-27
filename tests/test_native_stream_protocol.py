#!/usr/bin/env python3
"""Network-free protocol test for cache-backed PCM streaming."""

from __future__ import annotations

import base64
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asr_daemon as daemon  # noqa: E402


class FakeStreamingAdapter:
    is_streaming = True

    def __init__(self) -> None:
        self.reset_count = 0
        self.calls: list[dict] = []

    def reset_stream(self) -> None:
        self.reset_count += 1

    def transcribe(self, audio: np.ndarray, **kwargs: object) -> dict:
        self.calls.append({"audio": audio.copy(), **kwargs})
        return {"text": "测试", "language": "zh", "streaming": True}


def main() -> int:
    samples = np.array([0, 1_000, -1_000, 32_767], dtype="<i2")
    encoded = base64.b64encode(samples.tobytes()).decode("ascii")
    decoded = daemon.decode_pcm16(encoded)
    assert decoded.dtype == np.float32
    assert len(decoded) == len(samples)
    assert decoded[-1] > 0.99

    fake = FakeStreamingAdapter()
    daemon.MODEL_BACKEND = "paraformer"
    daemon.MODEL_NAME = "paraformer:test-streaming"
    daemon.PARAFORMER_ADAPTER = fake
    daemon.SAFE_LIVE_DRAFT = True
    daemon.is_untrusted_preview_text = lambda *_args: False
    daemon.load_glossary = lambda *_args, **_kwargs: []
    daemon.apply_corrections = lambda text, _path: (text, [])

    started = daemon.stream_start_request({"id": 1, "session": "test-session"})
    assert started["streaming"] is True
    assert fake.reset_count == 1

    result = daemon.stream_chunk_request({
        "id": 2,
        "session": "test-session",
        "pcm16": encoded,
    })
    assert result["text"] == "测试"
    assert result["streaming"] is True
    assert result["revision"] == 1
    assert fake.calls[0]["window_start"] == 0.0
    assert np.allclose(fake.calls[0]["audio"], decoded)

    cancelled = daemon.stream_cancel_request({"id": 3, "session": "test-session"})
    assert cancelled["event"] == "stream_cancelled"
    assert daemon.STREAM_SESSION_ID is None
    assert fake.reset_count == 2
    print("native stream protocol tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
