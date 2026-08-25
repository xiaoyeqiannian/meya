#!/usr/bin/env python3
"""Network-free tests for the P0 hotword A/B metrics and configuration."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.p0_hotword_ab import (  # noqa: E402
    Sample,
    character_error_rate,
    hotwords_for_config,
    load_references,
    normalized_text,
)
from glossary import GlossaryEntry  # noqa: E402


def main() -> int:
    references = load_references(PROJECT_ROOT / "evaluation/references.example.json")
    assert references["sample-001.wav"]["terms"] == ["NovaKit", "K8s"]
    assert normalized_text("把 NovaKit 部署到 K8s。") == normalized_text("把NovaKit部署到K8s")
    assert character_error_rate("把 NovaKit 部署到 K8s。", "把 NovaKit 部署到 K8s") == 0.0
    assert character_error_rate("甲乙丙", "甲乙丁") == 1 / 3

    entries = {
        "novakit": GlossaryEntry("NovaKit", ("诺瓦套件",)),
        "k8s": GlossaryEntry("K8s", ("k 八 s",)),
    }
    sample = Sample(
        audio="recordings/example.wav",
        duration=1.0,
        terms=("NovaKit", "K8s"),
    )
    all_forms = ["main", "诺瓦套件", "k 八 s", "DevPilot"]
    assert hotwords_for_config("no_hotword", all_forms, sample, entries) == []
    focused = hotwords_for_config("focused2", all_forms, sample, entries)
    assert focused == ["诺瓦套件", "k 八 s"]
    assert hotwords_for_config("all_effective", all_forms, sample, entries) == all_forms

    print("P0 evaluation tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
