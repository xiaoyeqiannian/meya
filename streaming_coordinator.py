"""Rolling-window merge for local Whisper. This is not a streaming decoder."""

from __future__ import annotations

from typing import Any


SAMPLE_RATE = 16_000
WINDOW_SECONDS = 6.0
MAX_WINDOW_SECONDS = 8.0
OVERLAP_SECONDS = 0.8
STABLE_TAIL_SECONDS = 0.8
FULL_FINAL_LIMIT = 60.0


def window_bounds(duration: float, committed_end: float) -> tuple[float, float]:
    """Return the next decode window in seconds: last 6s, overlapping committed audio."""
    if duration <= 0:
        return 0.0, 0.0
    start = max(0.0, committed_end - OVERLAP_SECONDS, duration - WINDOW_SECONDS)
    if start > duration:
        start = 0.0
    return round(start, 3), round(duration, 3)


def slice_audio(audio: Any, start: float, end: float, sample_rate: int = SAMPLE_RATE):
    i0 = max(0, int(round(start * sample_rate)))
    i1 = min(len(audio), int(round(end * sample_rate)))
    if i1 < i0:
        return audio[0:0]
    return audio[i0:i1]


def should_rerun_full(duration: float, committed_end: float | None = None) -> bool:
    if duration <= FULL_FINAL_LIMIT:
        return True
    if committed_end is not None and duration - committed_end > FULL_FINAL_LIMIT:
        return True
    return False


def longest_common_prefix(left: str, right: str) -> str:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return left[:index]


def join_committed_and_tail(committed: str, tail: str) -> str:
    if not committed:
        return tail
    if not tail:
        return committed
    overlap = min(len(committed), len(tail))
    for size in range(overlap, 1, -1):
        if committed.endswith(tail[:size]):
            return committed + tail[size:]
    if committed[-1].isascii() and committed[-1].isalnum() and tail[0].isascii() and tail[0].isalnum():
        return committed + " " + tail
    return committed + tail


def _join_segment_texts(segments: list[dict[str, Any]]) -> str:
    text = ""
    for segment in segments:
        piece = str(segment.get("text") or "").strip()
        if piece:
            text = join_committed_and_tail(text, piece)
    return text


def stabilize(
    *,
    committed_text: str,
    committed_end: float,
    last_hypothesis: str,
    window_start: float,
    window_end: float,
    window_text: str,
    segments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Commit a prefix that is either timestamp-stable or repeated across two windows."""
    absolute: list[dict[str, Any]] = []
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        absolute.append(
            {
                "start": window_start + float(segment.get("start") or 0.0),
                "end": window_start + float(segment.get("end") or 0.0),
                "text": text,
            }
        )

    cutoff = window_end - STABLE_TAIL_SECONDS
    stable = [item for item in absolute if item["end"] <= cutoff + 1e-6]
    tail = [item for item in absolute if item["end"] > cutoff + 1e-6]

    new_committed = committed_text
    new_end = committed_end
    if stable:
        new_committed = join_committed_and_tail(committed_text, _join_segment_texts(stable))
        new_end = max(committed_end, stable[-1]["end"])

    window_text = (window_text or "").strip()
    previous = (last_hypothesis or "").strip()
    if previous and window_text:
        shared = longest_common_prefix(previous, window_text).strip()
        if len(shared) >= 2:
            candidate = join_committed_and_tail(committed_text, shared)
            if len(candidate) > len(new_committed):
                new_committed = candidate
                if new_end <= committed_end:
                    new_end = max(committed_end, min(cutoff, window_start + (window_end - window_start) * 0.5))

    tail_text = _join_segment_texts(tail)
    display = join_committed_and_tail(new_committed, tail_text)
    if not display:
        display = join_committed_and_tail(committed_text, window_text)
    return {
        "committed_text": new_committed,
        "committed_end": new_end,
        "tail_text": tail_text,
        "display_text": display,
        "last_hypothesis": window_text,
    }
