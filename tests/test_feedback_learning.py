#!/usr/bin/env python3
"""Network-free tests for conservative local feedback learning."""

from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from feedback_learning import (  # noqa: E402
    infer_replacements,
    list_learning_rules,
    process_feedback,
    rollback_learning_rule,
)
from glossary import GlossaryEntry, load_glossary, serialize_glossary  # noqa: E402


def main() -> int:
    entries = [GlossaryEntry("NovaKit"), GlossaryEntry("K8s"), GlossaryEntry("DevPilot")]
    assert infer_replacements("部署诺瓦到K8s", "部署NovaKit到K8s", entries) == [("诺瓦", "NovaKit")]
    assert infer_replacements("部署novacat到K8s", "部署NovaKit到K8s", entries) == [("novacat", "NovaKit")]
    assert infer_replacements("部署NovaKit", "部署NovaKit然后测试", entries) == []
    assert infer_replacements(
        "把netas部署到K8s",
        "把Nydus部署到K8s",
        entries,
        include_new_terms=True,
    ) == [("netas", "Nydus")]

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        glossary = root / "glossary.tsv"
        glossary.write_text(serialize_glossary(entries), encoding="utf-8")
        for confirmation in (1, 2):
            event = process_feedback(
                expected="部署诺瓦到K8s",
                edited="部署NovaKit到K8s",
                raw_text="部署诺瓦到K8s",
                final_text="部署诺瓦到K8s",
                audio_path="recording.wav",
                app_name="CodeEditor",
                entries=load_glossary(glossary),
                glossary_path=glossary,
                user_data_dir=root,
            )
            assert event["observed"][0]["confirmations"] == confirmation
            assert not {"raw_text", "final_text", "audio_path", "app_name"} & event.keys()
        learned = load_glossary(glossary)
        assert learned[0].mistakes == ("诺瓦",)

        accepted = process_feedback(
            expected="使用DevPilot",
            edited="使用DevPilot",
            raw_text="使用 dev pilot",
            final_text="使用DevPilot",
            audio_path="recording.wav",
            app_name="CodeEditor",
            entries=learned,
            glossary_path=glossary,
            user_data_dir=root,
        )
        assert accepted["accepted_unchanged"] is True

        explicit = process_feedback(
            expected="使用dev pilet",
            edited="使用DevPilot",
            raw_text="使用dev pilet",
            final_text="使用dev pilet",
            audio_path="recording.wav",
            app_name="CodeEditor",
            entries=load_glossary(glossary),
            glossary_path=glossary,
            user_data_dir=root,
            explicit=True,
        )
        assert explicit["activated"][0]["canonical"] == "DevPilot"
        assert load_glossary(glossary)[2].mistakes == ("dev pilet",)

        new_term = process_feedback(
            expected="使用polarisctl发布",
            edited="使用NebulaCLI发布",
            raw_text="使用polarisctl发布",
            final_text="使用polarisctl发布",
            audio_path="recording.wav",
            app_name="CodeEditor",
            entries=load_glossary(glossary),
            glossary_path=glossary,
            user_data_dir=root,
            explicit=True,
        )
        assert new_term["activated"][0]["canonical"] == "NebulaCLI"
        added = next(entry for entry in load_glossary(glossary) if entry.canonical == "NebulaCLI")
        assert added.mistakes == ("polarisctl",)
        assert (root / "feedback-events.jsonl").read_text(encoding="utf-8").count("\n") == 5

        rules = list_learning_rules(root)
        learned_nova = next(rule for rule in rules if rule["canonical"] == "NovaKit" and rule["observed"] == "诺瓦")
        assert learned_nova["confirmations"] == 2
        assert learned_nova["hit_count"] == 0
        rollback_learning_rule(
            rule_id=learned_nova["id"],
            entries=load_glossary(glossary),
            glossary_path=glossary,
            user_data_dir=root,
        )
        assert "诺瓦" not in load_glossary(glossary)[0].mistakes
        assert learned_nova["id"] not in {rule["id"] for rule in list_learning_rules(root)}

    print("feedback learning tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
