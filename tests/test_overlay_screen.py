#!/usr/bin/env python3
"""The recording overlay must follow the focused input field's display."""

from pathlib import Path


def main() -> int:
    source = (Path(__file__).resolve().parents[1] / "app/LocalVoiceInput.swift").read_text()
    required = [
        "FocusedInputScreenLocator",
        "kAXFocusedUIElementAttribute",
        "kAXSelectedTextRangeAttribute",
        "kAXBoundsForRangeParameterizedAttribute",
        "CGDisplayBounds",
        "recent_global_click",
        "noteGlobalClick()",
        "isUsableCaretRect",
        "frontmost_window",
        "overlay-screen.json",
        "beginPlacementSession()",
        "placementScreen = nil",
    ]
    missing = [item for item in required if item not in source]
    if missing:
        print(f"FAIL: missing focused-screen overlay behavior: {missing}")
        return 1

    position_start = source.index("private func positionPanel()")
    position_end = source.index("\n    }", position_start)
    position_source = source[position_start:position_end]
    if "NSScreen.main ?? NSScreen.screens.first" in position_source:
        print("FAIL: overlay placement still hard-codes the main display")
        return 1

    print("overlay screen tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
