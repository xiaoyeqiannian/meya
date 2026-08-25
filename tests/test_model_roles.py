#!/usr/bin/env python3
"""Model management keeps independently selected preview and final roles."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from model_roles import (  # noqa: E402
    discover_cached_models,
    model_for_pass,
    resolve_preview_model,
)


def main() -> int:
    failures = 0

    cached = [
        "mlx-community/whisper-large-v3-mlx",
        "mlx-community/whisper-large-v3-turbo",
        "mlx-community/whisper-large-v3-turbo-4bit",
        "mlx-community/whisper-large-v3-4bit",
        "mlx-community/belle-whisper-large-v3-zh",
    ]
    preview = resolve_preview_model("mlx-community/whisper-large-v3-mlx", cached)
    if preview != "mlx-community/whisper-large-v3-turbo-4bit":
        print(f"FAIL: expected turbo-4bit preview, got {preview}")
        failures += 1

    if resolve_preview_model("mlx-community/whisper-large-v3-4bit", cached) != (
        "mlx-community/whisper-large-v3-turbo-4bit"
    ):
        print("FAIL: preview should stay on the smallest turbo even if user picked 4bit large")
        failures += 1

    no_small = ["mlx-community/whisper-large-v3-mlx"]
    if resolve_preview_model("mlx-community/whisper-large-v3-mlx", no_small) != (
        "mlx-community/whisper-large-v3-mlx"
    ):
        print("FAIL: missing preview cache should fall back to the final model")
        failures += 1

    if model_for_pass(False, "preview-model", "final-model") != "preview-model":
        print("FAIL: live pass must use the preview model")
        failures += 1
    if model_for_pass(True, "preview-model", "final-model") != "final-model":
        print("FAIL: final pass must use the refine model")
        failures += 1

    hub = Path(__file__).resolve().parents[1] / "models" / "huggingface" / "hub"
    if hub.exists():
        discovered = discover_cached_models(hub)
        if "mlx-community/whisper-large-v3-turbo-4bit" not in discovered:
            print(f"FAIL: did not discover turbo-4bit in {discovered}")
            failures += 1
        if "mlx-community/whisper-large-v3-mlx" not in discovered:
            print(f"FAIL: did not discover large-v3-mlx in {discovered}")
            failures += 1

    daemon = Path(__file__).resolve().parents[1] / "asr_daemon.py"
    daemon_source = daemon.read_text(encoding="utf-8")
    if "threading.Thread" in daemon_source:
        print("FAIL: asr_daemon must not load a second model on a background thread")
        failures += 1
    if "install_model_holder_cache" in daemon_source:
        print("FAIL: one daemon process should hold only one Whisper model")
        failures += 1

    swift = Path(__file__).resolve().parents[1] / "app" / "LocalVoiceInput.swift"
    swift_source = swift.read_text(encoding="utf-8")
    if "setProvisionalText" in swift_source or "beginMarkedTextSession" in swift_source:
        print("FAIL: underline / input-source switching code should be gone")
        failures += 1
    if "liveDraftInserter.update" not in swift_source:
        print("FAIL: live preview should update the active field through the safe draft inserter")
        failures += 1
    draft_start = swift_source.find("private final class LiveDraftInserter")
    draft_end = swift_source.find("@objc(LocalVoiceInputController)", draft_start)
    draft_source = swift_source[draft_start:draft_end]
    if ".maskCommand" in draft_source or "postKey(" in draft_source:
        print("FAIL: live preview must not synthesize Command shortcuts")
        failures += 1
    if "NSPasteboard" in draft_source:
        print("FAIL: live preview must not replace the clipboard")
        failures += 1
    if "insertUsingAccessibility" not in swift_source:
        print("FAIL: final insertion should prefer AXSelectedText before Command-V")
        failures += 1
    if "精修中" in swift_source:
        print("FAIL: overlay should keep 识别中, not 精修中")
        failures += 1
    if "refineService" not in swift_source or "previewService" not in swift_source:
        print("FAIL: preview and final models must run in separate processes")
        failures += 1
    if "whisper-large-v3-turbo-4bit" not in swift_source:
        print("FAIL: Swift preview picker should prefer turbo-4bit")
        failures += 1
    if "minPartialSamples" not in swift_source or "partialPollInterval" not in swift_source:
        print("FAIL: live preview should wait for enough audio and use bounded polling")
        failures += 1
    if "lastDetectedLanguage" in swift_source:
        print("FAIL: do not lock later passes to a language guessed from a short clip")
        failures += 1
    if "previewModelPopup" not in swift_source or "finalModelPopup" not in swift_source:
        print("FAIL: model manager must provide separate live and final selectors")
        failures += 1
    if 'previewModel = "preview_model"' not in swift_source or 'finalModel = "final_model"' not in swift_source:
        print("FAIL: model config must persist both roles")
        failures += 1
    menu_start = swift_source.find("private func setupStatusItem()")
    menu_end = swift_source.find("private func setupFunctionKeyHold()", menu_start)
    menu_source = swift_source[menu_start:menu_end]
    if "所选模型：" in menu_source or "实时识别：加载中" in menu_source or "松手定稿：加载中" in menu_source:
        print("FAIL: status menu must not list model role rows")
        failures += 1

    if failures:
        print(f"{failures} failed")
        return 1
    print("model role tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
