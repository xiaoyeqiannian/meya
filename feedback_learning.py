#!/usr/bin/env python3
"""Local, conservative learning from user-confirmed post-insertion edits."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
from typing import Any

from feedback_store import FeedbackStore
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
    *,
    include_new_terms: bool = False,
) -> list[tuple[str, str]]:
    """Infer conservative replacements, optionally accepting explicit new terms."""
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
    if include_new_terms:
        for replacement in _infer_direct_replacements(expected, edited):
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


_ASCII_TERM_CHARACTER = re.compile(r"[A-Za-z0-9._+/-]")
_MEANINGFUL_CHARACTER = re.compile(r"[A-Za-z0-9\u3400-\u4DBF\u4E00-\u9FFF]")


def _expand_ascii_term(text: str, start: int, end: int) -> tuple[int, int]:
    """Expand a partial SequenceMatcher opcode to an ASCII term boundary."""
    while start > 0 and _ASCII_TERM_CHARACTER.fullmatch(text[start - 1]):
        start -= 1
    while end < len(text) and _ASCII_TERM_CHARACTER.fullmatch(text[end]):
        end += 1
    return start, end


def _infer_direct_replacements(expected: str, edited: str) -> list[tuple[str, str]]:
    """Infer explicit user-confirmed replacements for a new canonical term."""
    replacements: list[tuple[str, str]] = []
    matcher = SequenceMatcher(a=expected, b=edited, autojunk=False)
    for operation, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        if operation != "replace":
            continue
        segments = expected[left_start:left_end] + edited[right_start:right_end]
        if any(character.isascii() and character.isalnum() for character in segments):
            left_start, left_end = _expand_ascii_term(expected, left_start, left_end)
            right_start, right_end = _expand_ascii_term(edited, right_start, right_end)
        source = _clean_segment(expected[left_start:left_end])
        canonical = _clean_segment(edited[right_start:right_end])
        if (
            source
            and canonical
            and source.casefold() != canonical.casefold()
            and _MEANINGFUL_CHARACTER.search(source)
            and _MEANINGFUL_CHARACTER.search(canonical)
            and 2 <= len(source) <= 32
            and 2 <= len(canonical) <= 32
            and len(source.split()) <= 4
            and len(canonical.split()) <= 4
        ):
            replacement = (source, canonical)
            if replacement not in replacements:
                replacements.append(replacement)
    return replacements


def accepted_terms(text: str, entries: list[GlossaryEntry]) -> list[str]:
    return [entry.canonical for entry in entries if _contains(text, entry.canonical)]


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
    store = FeedbackStore(user_data_dir)
    unchanged = expected == edited
    replacements = [] if unchanged else infer_replacements(
        expected,
        edited,
        entries,
        include_new_terms=explicit,
    )
    activated: list[LearnedCandidate] = []
    observed: list[LearnedCandidate] = []
    updated_entries = list(entries)
    for source, canonical in replacements:
        rule = store.observe_rule(
            canonical,
            source,
            explicit=explicit,
            activation_confirmations=ACTIVATION_CONFIRMATIONS,
        )
        count = int(rule["confirmations"])
        is_active = bool(rule["activated"])
        item = LearnedCandidate(canonical, source, count, is_active)
        observed.append(item)
        if is_active:
            entry = next(
                (entry for entry in updated_entries if entry.canonical.casefold() == canonical.casefold()),
                None,
            )
            existing = {value.casefold() for value in entry_forms(entry)} if entry else set()
            if source.casefold() not in existing:
                owns_canonical = entry is None
                updated_entries = add_variant(updated_entries, canonical, source, kind="mistake")
                store.mark_glossary_ownership(
                    int(rule["id"]),
                    variant=True,
                    canonical=owns_canonical,
                )
                activated.append(item)
    if updated_entries != entries:
        glossary_path.write_text(serialize_glossary(updated_entries), encoding="utf-8")
    if unchanged:
        for canonical in accepted_terms(final_text, updated_entries):
            store.accept_term(canonical)

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


def list_learning_rules(user_data_dir: Path) -> list[dict[str, Any]]:
    """Return review-safe learned-rule evidence without retained utterance text."""
    return FeedbackStore(user_data_dir).list_rules()


def rollback_learning_rule(
    *,
    rule_id: int,
    entries: list[GlossaryEntry],
    glossary_path: Path,
    user_data_dir: Path,
) -> dict[str, Any]:
    """Revert one learned mapping while preserving manually-owned glossary data."""
    store = FeedbackStore(user_data_dir)
    rule = store.get_rule(rule_id)
    if rule is None:
        raise ValueError("学习规则不存在或已撤销")
    updated_entries = list(entries)
    if bool(rule["owns_glossary_variant"]):
        canonical_key = str(rule["canonical_key"])
        observed_key = str(rule["observed_key"])
        for index, entry in enumerate(updated_entries):
            if entry.canonical.casefold() != canonical_key:
                continue
            mistakes = tuple(value for value in entry.mistakes if value.casefold() != observed_key)
            updated_entries[index] = GlossaryEntry(entry.canonical, entry.aliases, mistakes)
            if (
                bool(rule["owns_glossary_canonical"])
                and not updated_entries[index].aliases
                and not updated_entries[index].mistakes
            ):
                updated_entries.pop(index)
            break
    if updated_entries != entries:
        glossary_path.write_text(serialize_glossary(updated_entries), encoding="utf-8")
    store.mark_reverted(rule_id)
    event = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "event": "rule_reverted",
        "rule_id": rule_id,
        "canonical": rule["canonical"],
        "observed": rule["observed"],
    }
    _append_event(user_data_dir / "feedback-events.jsonl", event)
    return event
