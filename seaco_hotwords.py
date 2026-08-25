#!/usr/bin/env python3
"""Compile glossary entries into hotwords SeACo can actually tokenize."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import re
import tempfile

from glossary import GlossaryEntry, automatic_pronunciation_forms


SEACO_PATTERN = re.compile(r"^[\u4E00-\u9FA50-9]+$")


@dataclass(frozen=True)
class HotwordForm:
    text: str
    source: str
    status: str
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class HotwordEntryReport:
    canonical: str
    status: str
    effective_forms: tuple[str, ...]
    rejected_forms: tuple[str, ...]


@dataclass(frozen=True)
class HotwordCompilation:
    selected_hotwords: tuple[str, ...]
    entries: tuple[HotwordEntryReport, ...]

    @property
    def effective_entries(self) -> int:
        return sum(entry.status == "effective" for entry in self.entries)

    @property
    def partial_entries(self) -> int:
        return sum(entry.status == "partial_unknown" for entry in self.entries)

    @property
    def unknown_entries(self) -> int:
        return sum(entry.status == "unknown" for entry in self.entries)


@lru_cache(maxsize=2)
def load_seg_dict(path: str) -> dict[str, tuple[str, ...]]:
    """Load FunASR's word-to-token map once per final model."""
    result: dict[str, tuple[str, ...]] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        columns = line.strip().split()
        if columns:
            result[columns[0]] = tuple(columns[1:])
    return result


def seaco_tokens(text: str, seg_dict: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    """Mirror SeacoParaformer's embedded generate_hotwords_list tokenizer."""
    output: list[str] = []
    for raw_word in text.strip().split():
        word = raw_word.lower()
        if word in seg_dict:
            output.extend(seg_dict[word])
        elif SEACO_PATTERN.fullmatch(word):
            for character in word:
                output.extend(seg_dict.get(character, ("<unk>",)))
        else:
            output.append("<unk>")
    return tuple(output)


def analyze_form(
    text: str,
    source: str,
    seg_dict: dict[str, tuple[str, ...]],
) -> HotwordForm:
    tokens = seaco_tokens(text, seg_dict)
    unknown = sum(token == "<unk>" for token in tokens)
    if tokens and unknown == 0:
        status = "effective"
    elif tokens and unknown < len(tokens):
        status = "partial_unknown"
    else:
        status = "unknown"
    return HotwordForm(text, source, status, tokens)


def compile_glossary(
    entries: list[GlossaryEntry],
    seg_dict_path: Path,
    *,
    max_terms: int = 100,
    max_chars: int = 1_000,
    max_forms_per_entry: int | None = None,
) -> HotwordCompilation:
    seg_dict = load_seg_dict(str(seg_dict_path.resolve()))
    analyzed: list[tuple[GlossaryEntry, list[HotwordForm]]] = []
    for entry in entries:
        candidates: list[tuple[str, str]] = [(entry.canonical, "canonical")]
        candidates.extend((alias, "pronunciation") for alias in entry.aliases)
        candidates.extend(
            (value, "automatic") for value in automatic_pronunciation_forms(entry.canonical)
        )
        seen: set[str] = set()
        forms: list[HotwordForm] = []
        for value, source in candidates:
            value = " ".join(value.strip().split())
            key = value.casefold()
            if value and key not in seen:
                forms.append(analyze_form(value, source, seg_dict))
                seen.add(key)
        analyzed.append((entry, forms))

    # Preserve every effective canonical spelling before pronunciation forms.
    ordered = [
        (index, form)
        for index, (_, forms) in enumerate(analyzed)
        for form in forms
        if form.source == "canonical"
    ]
    ordered.extend(
        (index, form)
        for index, (_, forms) in enumerate(analyzed)
        for form in forms
        if form.source != "canonical"
    )
    selected: list[str] = []
    selected_keys: set[str] = set()
    selected_by_entry: dict[int, int] = {}
    length = 0
    for entry_index, form in ordered:
        key = form.text.casefold()
        added = len(form.text) + (1 if selected else 0)
        if form.status != "effective" or key in selected_keys:
            continue
        if (
            max_forms_per_entry is not None
            and selected_by_entry.get(entry_index, 0) >= max_forms_per_entry
        ):
            continue
        if len(selected) >= max_terms or length + added > max_chars:
            continue
        selected.append(form.text)
        selected_keys.add(key)
        selected_by_entry[entry_index] = selected_by_entry.get(entry_index, 0) + 1
        length += added

    reports: list[HotwordEntryReport] = []
    for entry, forms in analyzed:
        effective = tuple(form.text for form in forms if form.status == "effective")
        rejected = tuple(form.text for form in forms if form.status != "effective")
        if effective:
            status = "effective"
        elif any(form.status == "partial_unknown" for form in forms):
            status = "partial_unknown"
        else:
            status = "unknown"
        reports.append(HotwordEntryReport(entry.canonical, status, effective, rejected))
    return HotwordCompilation(tuple(selected), tuple(reports))


def write_compilation_report(
    path: Path,
    compilation: HotwordCompilation,
    *,
    model: str,
) -> None:
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "summary": {
            "entries": len(compilation.entries),
            "effective_entries": compilation.effective_entries,
            "partial_entries": compilation.partial_entries,
            "unknown_entries": compilation.unknown_entries,
            "selected_hotwords": len(compilation.selected_hotwords),
        },
        "selected_hotwords": list(compilation.selected_hotwords),
        "entries": [asdict(entry) for entry in compilation.entries],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(body)
        temporary = Path(handle.name)
    temporary.replace(path)
