#!/usr/bin/env python3
"""Regression guard for the balanced low-latency live preview profile."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    swift = (ROOT / "app/LocalVoiceInput.swift").read_text(encoding="utf-8")
    adapter = (ROOT / "asr_adapters.py").read_text(encoding="utf-8")
    assert "static let minPartialSamples = 8_000" in swift
    assert "static let partialPollInterval = 0.2" in swift
    assert "static let firstPartialDelay = 0.15" in swift
    assert "STREAMING_CHUNK_SIZE = [0, 8, 4]" in adapter
    assert "STREAMING_CHUNK_SAMPLES = STREAMING_CHUNK_SIZE[1] * 960" in adapter
    assert "partialInFlight" in swift and "partialQueued" in swift
    print("live latency tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
