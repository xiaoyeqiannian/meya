#!/usr/bin/env python3
"""Regression guards for pointer-safe inline draft replacement."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    swift = (ROOT / "app/LocalVoiceInput.swift").read_text(encoding="utf-8")
    assert "liveDraftPointerPauseUntil" in swift
    assert "notePointerInteractionDuringRecording" in swift
    assert "[.leftMouseDown, .rightMouseDown, .otherMouseDown]" in swift
    assert "Date().addingTimeInterval(0.35)" in swift
    assert "selectionRangeIsSettable = selectedRangeIsSettable(focused)" in swift
    assert "if selectionIsKnown, selectionRangeIsSettable, let target" in swift
    assert "targetStillFocused(target), ownsCurrentDraft(target)" in swift
    assert "setSelectedRange(ownedRange, on: target) && postUnicode(replacement)" in swift
    assert "unicode_fallback_draft_moved" in swift
    assert "maxDirectAccessibilityCommitUTF16 = 160" in swift
    assert "return replaceUsingKeyboard(with: text, finish: true)" in swift
    assert "unicodeEventChunkLength = 20" in swift
    assert "Let Electron process the posted Unicode event" in swift
    print("pointer draft safety tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
