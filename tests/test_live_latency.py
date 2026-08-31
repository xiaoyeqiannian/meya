#!/usr/bin/env python3
"""Regression guards for native PCM streaming and its rolling fallback."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    swift = (ROOT / "app/LocalVoiceInput.swift").read_text(encoding="utf-8")
    framed = (ROOT / "app/MeyaFramedProtocol.swift").read_text(encoding="utf-8")
    adapter = (ROOT / "asr_adapters.py").read_text(encoding="utf-8")
    daemon = (ROOT / "asr_daemon.py").read_text(encoding="utf-8")
    # Non-streaming preview models keep the rolling-window fallback.
    assert "static let minPartialSamples = 8_000" in swift
    assert "static let partialPollInterval = 0.2" in swift
    assert "static let firstPartialDelay = 0.15" in swift
    # Native Paraformer bypasses the timer and temporary WAV files.
    assert "var onSamples: (([Float]) -> Void)?" in swift
    assert "beginStreaming(sessionID:" in swift
    assert '"command": "stream_start"' in swift
    assert "sendAudioOnQueue(" in swift
    assert "type: .audioPCM16" in swift
    assert '"command": "stream_cancel"' in swift
    assert "if previewNativeStreaming, let previewService" in swift
    assert "decode_pcm16" in daemon
    assert '"command": "stream_chunk"' in daemon
    assert '"--framed-stdio"' not in swift
    assert 'Data("MEYA".utf8)' in framed
    assert "static let headerSize = 36" in framed
    assert "STREAMING_CHUNK_SIZE = [0, 8, 4]" in adapter
    assert "STREAMING_CHUNK_SAMPLES = STREAMING_CHUNK_SIZE[1] * 960" in adapter
    assert 'if not self.is_streaming:\n            options["ncpu"]' in adapter
    assert "partialInFlight" in swift and "partialQueued" in swift
    print("live latency tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
