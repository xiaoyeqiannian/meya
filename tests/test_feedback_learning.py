#!/usr/bin/env python3
"""Network-free tests for conservative local feedback learning."""

from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from feedback_learning import infer_replacements, process_feedback  # noqa: E402
from glossary import GlossaryEntry, load_glossary, serialize_glossary  # noqa: E402


def main() -> int:
    entries = [GlossaryEntry("NovaKit"), GlossaryEntry("K8s"), GlossaryEntry("DevPilot")]
    assert infer_replacements("部署诺瓦到K8s", "部署NovaKit到K8s", entries) == [("诺瓦", "NovaKit")]
    assert infer_replacements("部署NovaKit", "部署NovaKit然后测试", entries) == []

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
        assert (root / "feedback-events.jsonl").read_text(encoding="utf-8").count("\n") == 3

    print("feedback learning tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
