#!/usr/bin/env python3
"""Compile glossary entries into hotwords SeACo can actually tokenize."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
from itertools import product
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
    pronunciation_suggestions: tuple[str, ...]


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


# These are suggestions, not automatic glossary rewrites.  Keep the table small
# and predictable; unknown words still get the spelled-letter fallback below.
TECHNICAL_PRONUNCIATIONS: dict[str, tuple[str, ...]] = {
    "api": ("艾皮艾",),
    "bohr": ("玻尔",),
    "bohrium": ("博瑞姆", "玻尔"),
    "build": ("比尔德",),
    "buildkit": ("比尔德凯特",),
    "cd": ("西迪",),
    "ci": ("西艾",),
    "cli": ("西艾勒艾",),
    "containerd": ("康坦纳迪",),
    "core": ("科尔",),
    "docker": ("道克尔",),
    "dockerfile": ("道克尔发奥",),
    "eci": ("伊西艾",),
    "file": ("发奥",),
    "fs": ("艾弗艾丝",),
    "git": ("吉特",),
    "gitlab": ("吉特莱布",),
    "grafana": ("格拉法纳",),
    "kubernetes": ("库伯内蒂斯",),
    "kruise": ("克鲁斯",),
    "lab": ("莱布",),
    "lebesgue": ("勒贝格",),
    "nacos": ("纳科斯",),
    "open": ("欧盆",),
    "openapi": ("欧盆艾皮艾",),
    "openkruise": ("欧盆克鲁斯",),
    "overlay": ("欧沃雷",),
    "overlayfs": ("欧沃雷艾弗艾丝",),
    "sandbox": ("沙箱", "三德博克斯"),
    "snapshot": ("斯纳普肖特",),
    "snapshotter": ("斯纳普肖特尔",),
    "utility": ("尤提里提",),
}

LETTER_PRONUNCIATIONS = {
    "a": "诶", "b": "比", "c": "西", "d": "迪", "e": "伊", "f": "艾弗",
    "g": "吉", "h": "艾尺", "i": "艾", "j": "杰", "k": "凯", "l": "艾勒",
    "m": "艾姆", "n": "艾恩", "o": "欧", "p": "皮", "q": "丘", "r": "阿尔",
    "s": "艾丝", "t": "提", "u": "优", "v": "维", "w": "达不溜", "x": "艾克斯",
    "y": "歪", "z": "贼德",
}
DIGIT_PRONUNCIATIONS = dict(zip("0123456789", "零一二三四五六七八九"))
ASCII_RUN_PATTERN = re.compile(r"[A-Za-z0-9]+")
WORD_PART_PATTERN = re.compile(r"[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+|[0-9]+")


def _spell_ascii(value: str) -> str:
    return "".join(
        LETTER_PRONUNCIATIONS.get(character.lower(), DIGIT_PRONUNCIATIONS.get(character, ""))
        for character in value
    )


def _ascii_run_suggestions(value: str) -> tuple[str, ...]:
    direct = TECHNICAL_PRONUNCIATIONS.get(value.casefold(), ())
    parts = WORD_PART_PATTERN.findall(value)
    combined: list[str] = []
    if len(parts) > 1:
        choices = [
            TECHNICAL_PRONUNCIATIONS.get(part.casefold(), (_spell_ascii(part),))[:2]
            for part in parts
        ]
        combined.extend("".join(items) for items in product(*choices))
    spelled = _spell_ascii(value)
    return tuple(dict.fromkeys((*direct, *combined, spelled)))


def _replace_ascii_runs(value: str, limit: int = 6) -> tuple[str, ...]:
    matches = list(ASCII_RUN_PATTERN.finditer(value))
    if not matches:
        return (value,)
    choices = [_ascii_run_suggestions(match.group())[:2] for match in matches]
    output: list[str] = []
    for replacements in product(*choices):
        pieces: list[str] = []
        cursor = 0
        for match, replacement in zip(matches, replacements):
            pieces.append(value[cursor:match.start()])
            pieces.append(replacement)
            cursor = match.end()
        pieces.append(value[cursor:])
        candidate = "".join(pieces)
        candidate = re.sub(r"[-_/+.\s]+", "", candidate)
        if candidate:
            output.append(candidate)
        if len(output) >= limit:
            break
    return tuple(dict.fromkeys(output))


def pronunciation_suggestions(
    canonical: str,
    aliases: tuple[str, ...],
    seg_dict: dict[str, tuple[str, ...]],
    *,
    limit: int = 3,
) -> tuple[str, ...]:
    """Return only user-confirmable Chinese forms that SeACo can encode."""
    raw_candidates: list[str] = []
    for value in (*aliases, canonical):
        raw_candidates.extend(_replace_ascii_runs(value))
    seen: set[str] = set()
    suggestions: list[str] = []
    for candidate in raw_candidates:
        key = candidate.casefold()
        if (
            key in seen
            or candidate.casefold() == canonical.casefold()
            or not re.search(r"[\u4e00-\u9fa5]", candidate)
        ):
            continue
        seen.add(key)
        if analyze_form(candidate, "suggestion", seg_dict).status == "effective":
            suggestions.append(candidate)
            if len(suggestions) >= limit:
                break
    return tuple(suggestions)


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
        suggestions = (
            pronunciation_suggestions(entry.canonical, entry.aliases, seg_dict)
            if status != "effective"
            else ()
        )
        reports.append(
            HotwordEntryReport(entry.canonical, status, effective, rejected, suggestions)
        )
    return HotwordCompilation(tuple(selected), tuple(reports))


def write_compilation_report(
    path: Path,
    compilation: HotwordCompilation,
    *,
    model: str,
) -> None:
    payload = {
        "schema_version": 2,
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
