#!/usr/bin/env python3
"""Conservative per-utterance glossary selection for Meya.

The selector has no hard-coded industry vocabulary. It only activates entries
that are evidenced by the focused field, application identity, live draft, or
the user's recent accepted terms.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
import unicodedata
from typing import Iterable, Mapping

from glossary import (
    GlossaryEntry,
    automatic_pronunciation_forms,
    compact_cjk_spaces,
)


@dataclass(frozen=True)
class HotwordSelection:
    entries: tuple[GlossaryEntry, ...]
    acoustic_entries: tuple[GlossaryEntry, ...]
    reasons: Mapping[str, tuple[str, ...]]


def _searchable(text: str) -> str:
    return compact_cjk_spaces(unicodedata.normalize("NFKC", text)).casefold()


def _contains(text: str, form: str) -> bool:
    source = _searchable(form).strip()
    if not source:
        return False
    prefix = r"(?<![a-z0-9])" if source[0].isascii() and source[0].isalnum() else ""
    suffix = r"(?![a-z0-9])" if source[-1].isascii() and source[-1].isalnum() else ""
    return re.search(prefix + re.escape(source) + suffix, _searchable(text)) is not None


def entry_forms(entry: GlossaryEntry) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (
                entry.canonical,
                *entry.aliases,
                *entry.mistakes,
                *automatic_pronunciation_forms(entry.canonical),
            )
        )
    )


def select_hotword_entries(
    entries: Iterable[GlossaryEntry],
    *,
    context_text: str = "",
    draft_text: str = "",
    app_name: str = "",
    app_bundle: str = "",
    recent_terms: Mapping[str, int] | None = None,
    limit: int = 16,
) -> HotwordSelection:
    """Return only glossary entries with observable evidence for this utterance."""
    if limit <= 0:
        return HotwordSelection((), (), {})
    recent = {key.casefold(): max(0, int(value)) for key, value in (recent_terms or {}).items()}
    ranked: list[tuple[float, int, GlossaryEntry, tuple[str, ...], bool]] = []
    for index, entry in enumerate(entries):
        score = 0.0
        reasons: list[str] = []
        acoustic = False
        canonical = entry.canonical.casefold()
        if _contains(context_text, entry.canonical):
            score += 120.0
            reasons.append("context:canonical")
            acoustic = True
        elif any(_contains(context_text, form) for form in entry_forms(entry)[1:]):
            score += 105.0
            reasons.append("context:variant")
            acoustic = True
        if _contains(draft_text, entry.canonical):
            score += 80.0
            reasons.append("draft:canonical")
        elif any(_contains(draft_text, form) for form in entry_forms(entry)[1:]):
            score += 110.0
            reasons.append("draft:variant")
            acoustic = True
        app_match = _contains(app_name, entry.canonical) or _contains(app_bundle, entry.canonical)
        if app_match and score > 0:
            score += 20.0
            reasons.append("application")
        count = recent.get(canonical, 0)
        if count and score > 0:
            score += min(35.0, 10.0 + math.log2(count + 1) * 5.0)
            reasons.append("recent")
            acoustic = True
        if score > 0:
            ranked.append((score, index, entry, tuple(reasons), acoustic))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    chosen = ranked[:limit]
    return HotwordSelection(
        tuple(item[2] for item in chosen),
        tuple(item[2] for item in chosen if item[4]),
        {item[2].canonical: item[3] for item in chosen},
    )
