#!/usr/bin/env python3
"""Final recognition remains automatic while short live Chinese drafts are stabilized."""

from __future__ import annotations

import inspect
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe import (  # noqa: E402
    is_untrusted_preview_text,
    resolve_decode_language,
    resolve_whisper_language,
)


def main() -> int:
    failures = 0

    if resolve_whisper_language("auto") is not None:
        print("FAIL: auto should leave language undetected")
        failures += 1
    if resolve_whisper_language("") is not None:
        print("FAIL: empty language should autodetect")
        failures += 1
    if resolve_whisper_language("zh") != "zh":
        print("FAIL: explicit zh should still be allowed")
        failures += 1
    if resolve_decode_language("auto", "en") is not None:
        print("FAIL: auto must not reuse a previous English detect")
        failures += 1
    if resolve_decode_language("ja", "en") != "ja":
        print("FAIL: an explicit language must win over the cache")
        failures += 1
    if resolve_decode_language("auto", None) is not None:
        print("FAIL: first pass with no cache should still autodetect")
        failures += 1
    if not is_untrusted_preview_text("Thank you.", 0.4):
        print("FAIL: short English boilerplate should not be shown as live text")
        failures += 1
    if is_untrusted_preview_text("今天天气不错", 1.2):
        print("FAIL: real Chinese preview text should be kept")
        failures += 1
    if not is_untrusted_preview_text("Thanks for checking", 1.4, "zh"):
        print("FAIL: short ASCII-only text should be held back in Chinese live mode")
        failures += 1
    if not is_untrusted_preview_text("こんにちは", 1.8, "zh"):
        print("FAIL: short foreign-script hallucinations should be held back")
        failures += 1
    if is_untrusted_preview_text("我们用 OpenAI 做识别", 1.8, "zh"):
        print("FAIL: Chinese live text with English terms should be kept")
        failures += 1

    daemon = Path(__file__).resolve().parents[1] / "asr_daemon.py"
    source = daemon.read_text(encoding="utf-8")
    if 'language="zh"' in source or "language='zh'" in source:
        print("FAIL: asr_daemon itself should honor the request language")
        failures += 1
    if "resolve_decode_language" not in source and "resolve_whisper_language" not in source:
        print("FAIL: asr_daemon should resolve language instead of forcing zh")
        failures += 1

    transcribe_source = inspect.getsource(resolve_whisper_language)
    if "auto" not in transcribe_source:
        print("FAIL: resolve_whisper_language should treat auto as detect")
        failures += 1

    swift = (Path(__file__).resolve().parents[1] / "app" / "LocalVoiceInput.swift").read_text(encoding="utf-8")
    partial_start = swift.index("private func requestPartialTranscription()")
    final_start = swift.index("private func finishRecording()")
    partial_source = swift[partial_start:final_start]
    final_source = swift[final_start:swift.index("private func completeFinal")]
    if 'language: "zh"' not in partial_source:
        print("FAIL: live preview should explicitly request Chinese")
        failures += 1
    if 'language: "zh"' in final_source:
        print("FAIL: final recognition should remain automatic")
        failures += 1

    if failures:
        print(f"{failures} failed")
        return 1
    print("language tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
