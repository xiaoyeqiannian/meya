#!/usr/bin/env python3
"""Network-free tests for Meya's structured personal glossary."""

from __future__ import annotations

from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from glossary import (  # noqa: E402
    GlossaryEntry,
    add_variant,
    apply_glossary_corrections,
    automatic_pronunciation_forms,
    compact_cjk_spaces,
    glossary_hotwords,
    load_glossary,
    migrate_legacy,
    serialize_glossary,
)


def main() -> int:
    entries = [
        GlossaryEntry("K8s", ("K 八 S",), ("K八S",)),
        GlossaryEntry("NovaKit", ("诺瓦套件",), ("nova cat",)),
        GlossaryEntry("main"),
    ]
    assert glossary_hotwords(entries) == ["K8s", "NovaKit", "main", "K 八 S", "诺瓦套件"]

    corrected, changes = apply_glossary_corrections(
        "把 K八S 和 nova cat 合并到 main，不要修改 domain。",
        entries,
    )
    assert corrected == "把 K8s 和 NovaKit 合并到 main，不要修改 domain。"
    assert changes
    assert automatic_pronunciation_forms("K8s") == ("k 八 s", "k八s", "k eight s")
    assert compact_cjk_spaces("把 诺 瓦 套 件 部 署 到 k 八 s") == "把诺瓦套件部署到 k 八 s"
    assert compact_cjk_spaces("Request ID 和 API 网关") == "Request ID 和 API 网关"

    updated = add_variant(entries, "K8s", "K eight S", kind="alias")
    updated = add_variant(updated, "main 分支", "面粉时", kind="mistake")
    assert updated[0].aliases == ("K 八 S", "K eight S")
    assert updated[-1] == GlossaryEntry("main 分支", (), ("面粉时",))

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        glossary = root / "glossary.tsv"
        glossary.write_text(serialize_glossary(entries), encoding="utf-8")
        assert load_glossary(glossary) == entries

        terms = root / "terms.txt"
        corrections = root / "corrections.tsv"
        terms.write_text("CI/CD\nMeya\n", encoding="utf-8")
        corrections.write_text("CI CD\tCI/CD\n麦亚\tMeya\n", encoding="utf-8")
        migrated = migrate_legacy(terms, corrections)
        assert migrated == [
            GlossaryEntry("CI/CD", (), ("CI CD",)),
            GlossaryEntry("Meya", (), ("麦亚",)),
        ]

    print("Glossary tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
