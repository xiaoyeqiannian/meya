#!/usr/bin/env python3
"""Network-free checks for opt-in local fine-tuning sample storage."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import tempfile
import wave
from array import array

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training_data import (  # noqa: E402
    analyze_audio_quality,
    TrainingSampleRejected,
    TrainingSampleStore,
    extract_corrected_utterance,
)


def expect_rejected(callback) -> None:
    try:
        callback()
    except TrainingSampleRejected:
        return
    raise AssertionError("expected TrainingSampleRejected")


def main() -> int:
    assert extract_corrected_utterance(
        "前文：把奈达斯部署到K八S。",
        "前文：把 Nydus 部署到 K8s。",
        "把奈达斯部署到K八S。",
    ) == "把 Nydus 部署到 K8s。"
    assert extract_corrected_utterance("部署到K八S", "部署到K8s", "部署到K八S") == "部署到K8s"
    expect_rejected(lambda: extract_corrected_utterance("原句", "完全无关的一大段文字", "原句"))

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        recordings = root / "recordings/voice-input"
        recordings.mkdir(parents=True)
        audio = recordings / "sample.wav"
        pcm = array("h", [int(0.1 * 32767 * math.sin(2 * math.pi * 220 * i / 16_000)) for i in range(16_000)])
        with wave.open(str(audio), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16_000)
            output.writeframes(pcm.tobytes())
        store = TrainingSampleStore(root / "user-data", recordings)
        values = {
            "expected_text": "把奈达斯部署到K八S",
            "edited_text": "把 Nydus 部署到 K8s",
            "raw_text": "把netas部署到K八S",
            "final_text": "把奈达斯部署到K八S",
            "audio_path": str(audio),
            "model": "qwen:Qwen3-ASR-1.7B-4bit",
        }
        first = store.save_feedback(**values)
        second = store.save_feedback(**values)
        assert first["sample_id"] == second["sample_id"]
        rows = [json.loads(line) for line in store.manifest_path.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 1
        assert rows[0]["reference"] == "把 Nydus 部署到 K8s"
        assert rows[0]["training_ready"] is True
        assert rows[0]["quality_status"] == "clean"
        assert rows[0]["rms_dbfs"] < -15
        assert "app_name" not in rows[0]
        assert rows[0]["duration"] == 1.0
        assert (store.root / rows[0]["audio"]).is_file()

        pending = store.save_feedback(**{
            **values,
            "expected_text": "原句",
            "edited_text": "完全无关的一大段文字",
            "final_text": "原句",
        })
        assert pending["training_ready"] is False
        assert pending["label_status"] == "needs_review"
        assert pending["reference"] == ""
        assert pending["reference_candidate"] == "完全无关的一大段文字"

        outside = root / "outside.wav"
        outside.write_bytes(audio.read_bytes())
        expect_rejected(lambda: store.save_feedback(**{**values, "audio_path": str(outside)}))

    quiet = analyze_audio_quality([int(0.001 * 32767)] * 16_000)
    assert quiet["quality_status"] == "reject"
    assert "rms_below_-35_dbfs" in quiet["quality_reasons"]
    clipped = [0] * 16_000
    clipped[:32] = [32767] * 32
    clipped_quality = analyze_audio_quality(clipped)
    assert clipped_quality["quality_status"] == "reject"
    assert "clipping_ratio_over_0.1_percent" in clipped_quality["quality_reasons"]

    print("training data tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
