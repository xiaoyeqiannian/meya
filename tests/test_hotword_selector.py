#!/usr/bin/env python3
"""Network-free tests for conservative utterance-scoped hotword selection."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from glossary import GlossaryEntry  # noqa: E402
from hotword_selector import select_hotword_entries  # noqa: E402


def main() -> int:
    entries = [
        GlossaryEntry("NovaKit", ("诺瓦套件",), ("novacat",)),
        GlossaryEntry("K8s"),
        GlossaryEntry("DevPilot"),
        GlossaryEntry("Request ID"),
        GlossaryEntry("裸金属"),
    ]
    selected = select_hotword_entries(
        entries,
        draft_text="把 诺 瓦 套 件 部 署 到 k 八 s",
        limit=16,
    )
    assert [entry.canonical for entry in selected.entries] == ["NovaKit", "K8s"]
    assert [entry.canonical for entry in selected.acoustic_entries] == ["NovaKit", "K8s"]
    assert selected.reasons["NovaKit"] == ("draft:variant",)

    selected = select_hotword_entries(entries, app_name="DevPilot", limit=16)
    assert selected.entries == ()

    selected = select_hotword_entries(entries, draft_text="Request ID", limit=16)
    assert [entry.canonical for entry in selected.entries] == ["Request ID"]
    assert selected.acoustic_entries == ()

    selected = select_hotword_entries(entries, draft_text="这部分逻辑值如何实现", limit=16)
    assert selected.entries == ()

    selected = select_hotword_entries(entries, recent_terms={"NovaKit": 4}, limit=16)
    assert selected.entries == ()

    selected = select_hotword_entries(
        entries,
        draft_text="诺瓦套件",
        recent_terms={"NovaKit": 4},
        limit=16,
    )
    assert [entry.canonical for entry in selected.entries] == ["NovaKit"]
    assert "recent" in selected.reasons["NovaKit"]

    print("hotword selector tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
