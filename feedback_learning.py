#!/usr/bin/env python3
"""Local, conservative learning from user-confirmed post-insertion edits."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import tempfile
from typing import Any

from glossary import GlossaryEntry, add_variant, serialize_glossary
from hotword_selector import entry_forms


ACTIVATION_CONFIRMATIONS = 2


@dataclass(frozen=True)
class LearnedCandidate:
    canonical: str
    observed: str
    confirmations: int
    activated: bool


def _clean_segment(value: str) -> str:
    return value.strip(" \t\r\n，。！？；：,.!?;:()（）[]【】\"'“”‘’")


def _contains(text: str, value: str) -> bool:
    return value.casefold() in text.casefold()


def infer_replacements(
    expected: str,
    edited: str,
    entries: list[GlossaryEntry],
) -> list[tuple[str, str]]:
    """Infer only exact canonical replacements from narrow replace operations."""
    replacements: list[tuple[str, str]] = []
    matcher = SequenceMatcher(a=expected, b=edited, autojunk=False)
    for operation, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        if operation != "replace":
            continue
        edited_folded = edited.casefold()
        for entry in entries:
            canonical = entry.canonical
            canonical_folded = canonical.casefold()
            search_from = 0
            while True:
                canonical_start = edited_folded.find(canonical_folded, search_from)
                if canonical_start < 0:
                    break
                canonical_end = canonical_start + len(canonical)
                search_from = canonical_start + 1
                if canonical_end <= right_start or canonical_start >= right_end:
                    continue
                left_extension = max(0, right_start - canonical_start)
                right_extension = max(0, canonical_end - right_end)
                source_start = max(0, left_start - left_extension)
                source_end = min(len(expected), left_end + right_extension)
                source = _clean_segment(expected[source_start:source_end])
                if (
                    source
                    and source.casefold() != canonical_folded
                    and 2 <= len(source) <= 32
                    and len(source.split()) <= 4
                ):
                    replacement = (source, canonical)
                    if replacement not in replacements:
                        replacements.append(replacement)
    # SequenceMatcher can split one human edit into adjacent replace/insert
    # opcodes and yield overlapping sources. Keep the longest conservative
    # source per canonical term so one correction creates one learned form.
    best: dict[str, tuple[str, str]] = {}
    order: list[str] = []
    for source, canonical in replacements:
        key = canonical.casefold()
        if key not in best:
            order.append(key)
        previous = best.get(key)
        if previous is None or len(source) > len(previous[0]):
            best[key] = (source, canonical)
    return [best[key] for key in order]


def accepted_terms(text: str, entries: list[GlossaryEntry]) -> list[str]:
    return [entry.canonical for entry in entries if _contains(text, entry.canonical)]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _append_event(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def process_feedback(
    *,
    expected: str,
    edited: str,
    raw_text: str,
    final_text: str,
    audio_path: str,
    app_name: str,
    entries: list[GlossaryEntry],
    glossary_path: Path,
    user_data_dir: Path,
    explicit: bool = False,
) -> dict[str, Any]:
    """Persist local evidence and activate a mistake only after two confirmations."""
    unchanged = expected == edited
    replacements = [] if unchanged else infer_replacements(expected, edited, entries)
    candidate_path = user_data_dir / "feedback-candidates.json"
    candidate_payload = _read_json(candidate_path)
    candidates = candidate_payload.get("candidates", {})
    if not isinstance(candidates, dict):
        candidates = {}
    activated: list[LearnedCandidate] = []
    observed: list[LearnedCandidate] = []
    updated_entries = list(entries)
    for source, canonical in replacements:
        key = canonical.casefold() + "\t" + source.casefold()
        previous = candidates.get(key, {}) if isinstance(candidates.get(key), dict) else {}
        count = max(0, int(previous.get("confirmations", 0))) + 1
        is_active = explicit or count >= ACTIVATION_CONFIRMATIONS
        candidates[key] = {
            "canonical": canonical,
            "observed": source,
            "confirmations": count,
            "activated": is_active,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        item = LearnedCandidate(canonical, source, count, is_active)
        observed.append(item)
        if is_active:
            entry = next(
                (entry for entry in updated_entries if entry.canonical.casefold() == canonical.casefold()),
                None,
            )
            existing = {value.casefold() for value in entry_forms(entry)} if entry else set()
            if source.casefold() not in existing:
                updated_entries = add_variant(updated_entries, canonical, source, kind="mistake")
                activated.append(item)
    if updated_entries != entries:
        glossary_path.write_text(serialize_glossary(updated_entries), encoding="utf-8")
    _atomic_json(
        candidate_path,
        {"schema_version": 1, "activation_confirmations": ACTIVATION_CONFIRMATIONS, "candidates": candidates},
    )

    usage_path = user_data_dir / "hotword-usage.json"
    usage_payload = _read_json(usage_path)
    usage = usage_payload.get("accepted_terms", {})
    if not isinstance(usage, dict):
        usage = {}
    if unchanged:
        for canonical in accepted_terms(final_text, updated_entries):
            usage[canonical] = max(0, int(usage.get(canonical, 0))) + 1
    _atomic_json(usage_path, {"schema_version": 1, "accepted_terms": usage})

    # Persist only the minimal learning evidence. Full recognition text, audio
    # paths, and focused-application names remain transient in the worker.
    event = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "accepted_unchanged": unchanged,
        "explicit": explicit,
        "observed": [asdict(item) for item in observed],
        "activated": [asdict(item) for item in activated],
    }
    _append_event(user_data_dir / "feedback-events.jsonl", event)
    return event
