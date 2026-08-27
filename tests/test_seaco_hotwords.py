#!/usr/bin/env python3
"""Network-free tests for SeACo-aware hotword compilation."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from glossary import GlossaryEntry, apply_glossary_corrections  # noqa: E402
from seaco_hotwords import (  # noqa: E402
    analyze_form,
    compile_glossary,
    pronunciation_suggestions,
    seaco_tokens,
    write_compilation_report,
)


def main() -> int:
    seg_dict = {
        "main": ("ma@@", "in"),
        "ma@@": ("ma@@",),
        "in": ("in",),
        "k": ("k",),
        "s": ("s",),
        "eight": ("eight",),
        "八": ("八",),
        "诺": ("诺",),
        "瓦": ("瓦",),
        "套": ("套",),
        "件": ("件",),
    }
    assert seaco_tokens("main", seg_dict) == ("ma@@", "in")
    assert seaco_tokens("NovaKit", seg_dict) == ("<unk>",)
    assert seaco_tokens("k 八 s", seg_dict) == ("k", "八", "s")
    assert analyze_form("k八s", "automatic", seg_dict).status == "unknown"

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        seg_dict_path = root / "seg_dict"
        seg_dict_path.write_text(
            "\n".join(f"{key}\t{' '.join(value)}" for key, value in seg_dict.items()) + "\n",
            encoding="utf-8",
        )
        entries = [
            GlossaryEntry("main"),
            GlossaryEntry("NovaKit"),
            GlossaryEntry("K8s"),
        ]
        compilation = compile_glossary(entries, seg_dict_path)
        reports = {entry.canonical: entry for entry in compilation.entries}
        assert reports["main"].status == "effective"
        assert reports["NovaKit"].status == "unknown"
        assert reports["K8s"].status == "effective"
        assert "K8s" not in compilation.selected_hotwords
        assert "k 八 s" in compilation.selected_hotwords
        assert "k eight s" in compilation.selected_hotwords
        assert "k八s" not in compilation.selected_hotwords
        one_form = compile_glossary(entries, seg_dict_path, max_forms_per_entry=1)
        assert "k 八 s" in one_form.selected_hotwords
        assert "k eight s" not in one_form.selected_hotwords

        with_alias = compile_glossary(
            [GlossaryEntry("NovaKit", ("诺瓦套件",))],
            seg_dict_path,
        )
        assert with_alias.entries[0].status == "effective"
        assert with_alias.selected_hotwords == ("诺瓦套件",)
        corrected, _ = apply_glossary_corrections("部署诺瓦套件", [GlossaryEntry("NovaKit", ("诺瓦套件",))])
        assert corrected == "部署NovaKit"

        report = root / "report.json"
        write_compilation_report(report, compilation, model="test-seaco")
        payload = json.loads(report.read_text(encoding="utf-8"))
        assert payload["summary"]["effective_entries"] == 2
        assert payload["summary"]["unknown_entries"] == 1
        assert "pronunciation_suggestions" in payload["entries"][1]

        suggestion_characters = set("玻尔科博瑞姆库伯内蒂斯凯优比伊阿恩提艾")
        suggestion_dict = {character: (character,) for character in suggestion_characters}
        compound = pronunciation_suggestions(
            "bohrium-core",
            ("玻尔core",),
            suggestion_dict,
        )
        assert compound[0] == "玻尔科尔"
        assert 1 <= len(compound) <= 3
        assert all(
            analyze_form(value, "suggestion", suggestion_dict).status == "effective"
            for value in compound
        )
        kubernetes = pronunciation_suggestions("Kubernetes", (), suggestion_dict)
        assert kubernetes[0] == "库伯内蒂斯"

    actual = (
        Path(__file__).resolve().parents[1]
        / "models/paraformer/iic--speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/seg_dict"
    )
    if actual.exists():
        live_entries = [
            GlossaryEntry("main"),
            GlossaryEntry("NovaKit"),
            GlossaryEntry("K8s"),
            GlossaryEntry("bohrium-core", ("玻尔core",)),
            GlossaryEntry("OpenKruise"),
            GlossaryEntry("SDBX"),
            GlossaryEntry("ECI"),
            GlossaryEntry("BuildKit"),
            GlossaryEntry("Dockerfile"),
            GlossaryEntry("containerd"),
            GlossaryEntry("overlayfs"),
            GlossaryEntry("snapshotter"),
            GlossaryEntry("Kubernetes"),
            GlossaryEntry("OpenAPI"),
            GlossaryEntry("CI/CD"),
            GlossaryEntry("GitLab"),
            GlossaryEntry("Grafana"),
            GlossaryEntry("阿里 AKS"),
            GlossaryEntry("nacos"),
        ]
        live = compile_glossary(
            live_entries,
            actual,
        )
        statuses = {entry.canonical: entry.status for entry in live.entries}
        assert statuses["main"] == "effective"
        assert statuses["NovaKit"] == "unknown"
        assert statuses["K8s"] == "effective"
        assert "k 八 s" in live.selected_hotwords
        unresolved = [entry for entry in live.entries if entry.status != "effective"]
        assert unresolved
        assert all(1 <= len(entry.pronunciation_suggestions) <= 3 for entry in unresolved)

    print("SeACo hotword compiler tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
