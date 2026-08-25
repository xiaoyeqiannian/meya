#!/usr/bin/env python3
"""Personal terms should stay compact and useful for spoken dictation."""

from __future__ import annotations

import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from transcribe import is_speakable_term, load_terms


def main() -> int:
    accepted = ["Meya", "CI/CD", "NovaKit", "Kubernetes API"]
    rejected = [
        "A",
        "MR",
        "PIP_INDEX_URL",
        "/Users/example/project",
        "https://example.com",
        "这是一整句不应该进入个人语音词库的说明文字",
        "保存以后请立即重新加载并且马上生效",
    ]
    if not all(is_speakable_term(term) for term in accepted):
        print("FAIL: useful spoken term rejected")
        return 1
    if any(is_speakable_term(term) for term in rejected):
        print("FAIL: unhelpful term accepted")
        return 1

    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw) / "terms.txt"
        path.write_text("\n".join(["Meya", "meya", *[f"术语{i}" for i in range(120)]]) + "\n")
        terms = load_terms(path)
        if len(terms) != 100 or terms.count("Meya") != 1:
            print("FAIL: terms were not deduplicated and capped at 100")
            return 1

    print("term quality tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
