#!/usr/bin/env python3
"""Rolling-window ASR must stay bounded and only commit a stable prefix."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streaming_coordinator import (  # noqa: E402
    join_committed_and_tail,
    should_rerun_full,
    slice_audio,
    stabilize,
    window_bounds,
)


def main() -> int:
    failures = 0

    start, end = window_bounds(5.0, 0.0)
    if (start, end) != (0.0, 5.0):
        print(f"FAIL: short clip should decode in full, got {(start, end)}")
        failures += 1

    start, end = window_bounds(20.0, 0.0)
    if start != 14.0 or end != 20.0:
        print(f"FAIL: first long preview should be the last 6s, got {(start, end)}")
        failures += 1

    start, end = window_bounds(20.0, 16.0)
    if abs(start - 15.2) > 1e-6 or end != 20.0:
        print(f"FAIL: later window should overlap committed audio, got {(start, end)}")
        failures += 1

    start, end = window_bounds(30.0, 10.0)
    if end != 30.0 or abs(start - 24.0) > 1e-6:
        print(f"FAIL: long uncommitted tail should still only decode the last 6s, got {(start, end)}")
        failures += 1
    if should_rerun_full(70.0, committed_end=5.0) is not True:
        print("FAIL: if the stabilizer falls more than 60s behind, final should re-run the full clip")
        failures += 1

    audio = np.arange(16_000, dtype=np.float32)
    sliced = slice_audio(audio, 0.25, 0.75)
    if len(sliced) != 8_000 or sliced[0] != 4_000:
        print(f"FAIL: slice_audio bounds are wrong: len={len(sliced)} first={sliced[0] if len(sliced) else None}")
        failures += 1

    if join_committed_and_tail("今天天气", "天气不错") != "今天天气不错":
        print("FAIL: overlapping tail should be de-duplicated")
        failures += 1
    if join_committed_and_tail("hello", "world") != "hello world":
        print("FAIL: latin tokens should keep a space")
        failures += 1

    timed = stabilize(
        committed_text="",
        committed_end=0.0,
        last_hypothesis="",
        window_start=10.0,
        window_end=16.0,
        window_text="今天天气不错",
        segments=[
            {"start": 0.0, "end": 2.0, "text": "今天"},
            {"start": 2.0, "end": 4.0, "text": "天气"},
            {"start": 4.0, "end": 5.5, "text": "不错"},
        ],
    )
    if timed["committed_text"] != "今天天气":
        print(f"FAIL: timestamped prefix should commit, got {timed['committed_text']!r}")
        failures += 1
    if abs(timed["committed_end"] - 14.0) > 1e-6:
        print(f"FAIL: committed_end should follow last stable segment, got {timed['committed_end']}")
        failures += 1
    if timed["tail_text"] != "不错":
        print(f"FAIL: last 0.8s should stay uncommitted, got {timed['tail_text']!r}")
        failures += 1

    repeated = stabilize(
        committed_text="",
        committed_end=0.0,
        last_hypothesis="今天天气不错啊",
        window_start=0.0,
        window_end=4.0,
        window_text="今天天气很好",
        segments=[],
    )
    if not repeated["committed_text"].startswith("今天天气"):
        print(f"FAIL: repeated prefix should commit, got {repeated['committed_text']!r}")
        failures += 1

    if should_rerun_full(59.9) is not True or should_rerun_full(60.1) is not False:
        print("FAIL: clips up to 60s re-run in full; longer clips use the rolling tail")
        failures += 1

    daemon = Path(__file__).resolve().parents[1] / "asr_daemon.py"
    daemon_source = daemon.read_text(encoding="utf-8")
    if "from streaming_coordinator import" not in daemon_source and "streaming_coordinator" not in daemon_source:
        print("FAIL: asr_daemon should use the rolling-window coordinator")
        failures += 1

    swift = Path(__file__).resolve().parents[1] / "app" / "LocalVoiceInput.swift"
    swift_source = swift.read_text(encoding="utf-8")
    if "beginMarkedTextSession" in swift_source or "InputSourceSession" in swift_source:
        print("FAIL: do not switch the system input source for an underline")
        failures += 1
    if "showProvisionalDraft" in swift_source or "draftLabel" in swift_source:
        print("FAIL: overlay must not keep underline/draft UI")
        failures += 1
    if "setProvisionalText" in swift_source:
        print("FAIL: remove unused in-field underline composition")
        failures += 1
    if "window_bounds" not in swift_source and "RollingWindow" not in swift_source:
        print("FAIL: Swift should slice a rolling window instead of the full utterance")
        failures += 1
    if "CACHED_LANGUAGE" in daemon_source:
        print("FAIL: asr_daemon must not pin later windows to the first detected language")
        failures += 1
    if "def warmup_model" not in daemon_source:
        print("FAIL: asr_daemon should compile the model before the first utterance")
        failures += 1
    if "initial_prompt" not in daemon_source or "if final" not in daemon_source:
        print("FAIL: live preview should skip the terms prompt")
        failures += 1

    if failures:
        print(f"{failures} failed")
        return 1
    print("streaming coordinator tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
