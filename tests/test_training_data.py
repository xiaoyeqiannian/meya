#!/usr/bin/env python3
"""Network-free checks for opt-in local fine-tuning sample storage."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training_data import (  # noqa: E402
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
        with wave.open(str(audio), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16_000)
            output.writeframes(b"\0\0" * 16_000)
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

    print("training data tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
