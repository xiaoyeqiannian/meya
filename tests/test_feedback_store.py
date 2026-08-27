#!/usr/bin/env python3
"""Network-free tests for SQLite learning migration and time decay."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from feedback_store import FeedbackStore  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "feedback-candidates.json").write_text(
            json.dumps({
                "candidates": {
                    "novakit\tnovacat": {
                        "canonical": "NovaKit",
                        "observed": "novacat",
                        "confirmations": 2,
                        "activated": True,
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    }
                }
            }),
            encoding="utf-8",
        )
        (root / "hotword-usage.json").write_text(
            json.dumps({"accepted_terms": {"NovaKit": 4}}),
            encoding="utf-8",
        )
        store = FeedbackStore(root)
        rules = store.list_rules()
        assert len(rules) == 1
        assert rules[0]["evidence"] == "用户确认 2 次"
        migrated = store.get_rule(rules[0]["id"])
        # Legacy JSON did not record whether the app or the user created the
        # glossary value, so migration must never claim deletion ownership.
        assert migrated is not None
        assert migrated["owns_glossary_variant"] == 0
        assert store.recent_terms()["NovaKit"] > 0

        store.record_rule_hits([("novacat", "NovaKit")])
        assert store.list_rules()[0]["hit_count"] == 1

        old = datetime.now(timezone.utc) - timedelta(days=91)
        with sqlite3.connect(root / "learning.sqlite3") as database:
            database.execute(
                "UPDATE term_usage SET last_accepted_at = ? WHERE canonical_key = ?",
                (old.isoformat(), "novakit"),
            )
        assert store.recent_terms(now=datetime.now(timezone.utc)) == {}
        with sqlite3.connect(root / "learning.sqlite3") as database:
            retired = database.execute(
                "SELECT retired_at FROM term_usage WHERE canonical_key = 'novakit'"
            ).fetchone()[0]
        assert retired is not None

        store.accept_term("NovaKit")
        assert store.recent_terms()["NovaKit"] > 4.9

        store.mark_reverted(rules[0]["id"])
        relearned = store.observe_rule(
            "NovaKit",
            "novacat",
            explicit=False,
            activation_confirmations=2,
        )
        assert relearned["confirmations"] == 1
        assert relearned["activated"] == 0

    print("feedback store tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
