#!/usr/bin/env python3
"""Structured personal glossary for Meya's hotwords and safe corrections."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re


GLOSSARY_HEADER = "# 标准写法\t发音/近音别名（、分隔）\t常见识别错词（、分隔）"
_VARIANT_SEPARATOR = re.compile(r"[、,，;；]+")
_ALPHANUMERIC_TERM = re.compile(r"^[A-Za-z0-9]+$")
_DIGIT_ZH = dict(zip("0123456789", "零一二三四五六七八九"))
_DIGIT_EN = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}
_CJK_CHARACTER = r"\u3400-\u4DBF\u4E00-\u9FFF"


@dataclass(frozen=True)
class GlossaryEntry:
    canonical: str
    aliases: tuple[str, ...] = ()
    mistakes: tuple[str, ...] = ()


def automatic_pronunciation_forms(canonical: str) -> tuple[str, ...]:
    """Expand unambiguous mixed letter/number identifiers such as K8s."""
    value = _clean(canonical)
    if not _ALPHANUMERIC_TERM.fullmatch(value):
        return ()
    if not any(character.isalpha() for character in value) or not any(
        character.isdigit() for character in value
    ):
        return ()
    zh_parts: list[str] = []
    en_parts: list[str] = []
    for character in value.lower():
        if character.isdigit():
            zh_parts.append(_DIGIT_ZH[character])
            en_parts.append(_DIGIT_EN[character])
        else:
            zh_parts.append(character)
            en_parts.append(character)
    spaced_zh = " ".join(zh_parts)
    compact_zh = "".join(zh_parts)
    spaced_en = " ".join(en_parts)
    return split_variants("、".join((spaced_zh, compact_zh, spaced_en)))


def _clean(value: str) -> str:
    return " ".join(value.strip().split())


def compact_cjk_spaces(text: str) -> str:
    """Remove tokenizer spaces only when both neighbours are CJK characters."""
    return re.sub(
        rf"(?<=[{_CJK_CHARACTER}])[ \t\u3000]+(?=[{_CJK_CHARACTER}])",
        "",
        text,
    )


def split_variants(value: str) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    for candidate in _VARIANT_SEPARATOR.split(value):
        item = _clean(candidate)
        key = item.casefold()
        if item and "\t" not in item and key not in seen:
            values.append(item)
            seen.add(key)
    return tuple(values)


def load_glossary(path: Path) -> list[GlossaryEntry]:
    if not path.exists():
        return []
    entries: list[GlossaryEntry] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        columns = line.split("\t")
        canonical = _clean(columns[0])
        key = canonical.casefold()
        if not canonical or key in seen:
            continue
        aliases = split_variants(columns[1]) if len(columns) > 1 else ()
        mistakes = split_variants(columns[2]) if len(columns) > 2 else ()
        aliases = tuple(item for item in aliases if item.casefold() != key)
        mistakes = tuple(item for item in mistakes if item.casefold() != key)
        entries.append(GlossaryEntry(canonical, aliases, mistakes))
        seen.add(key)
    return entries


def serialize_glossary(entries: list[GlossaryEntry]) -> str:
    lines = [GLOSSARY_HEADER]
    seen: set[str] = set()
    for entry in entries:
        canonical = _clean(entry.canonical)
        key = canonical.casefold()
        if not canonical or key in seen:
            continue
        aliases = "、".join(split_variants("、".join(entry.aliases)))
        mistakes = "、".join(split_variants("、".join(entry.mistakes)))
        lines.append(f"{canonical}\t{aliases}\t{mistakes}")
        seen.add(key)
    return "\n".join(lines) + "\n"


def glossary_hotwords(
    entries: list[GlossaryEntry],
    *,
    max_terms: int = 100,
    max_chars: int = 1_000,
) -> list[str]:
    """Keep every canonical term ahead of aliases, preserving user priority."""
    candidates = [entry.canonical for entry in entries]
    candidates.extend(alias for entry in entries for alias in entry.aliases)
    selected: list[str] = []
    seen: set[str] = set()
    length = 0
    for raw in candidates:
        value = _clean(raw)
        key = value.casefold()
        added = len(value) + (1 if selected else 0)
        if not value or key in seen:
            continue
        if len(selected) >= max_terms or length + added > max_chars:
            break
        selected.append(value)
        seen.add(key)
        length += added
    return selected


def _replacement_pattern(source: str) -> re.Pattern[str]:
    escaped = re.escape(source)
    prefix = r"(?<![A-Za-z0-9])" if source[0].isascii() and source[0].isalnum() else ""
    suffix = r"(?![A-Za-z0-9])" if source[-1].isascii() and source[-1].isalnum() else ""
    return re.compile(prefix + escaped + suffix, re.IGNORECASE)


def apply_glossary_corrections(
    text: str,
    entries: list[GlossaryEntry],
) -> tuple[str, list[tuple[str, str]]]:
    """Normalize casing and confirmed aliases without touching larger ASCII words."""
    rules: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        canonical = _clean(entry.canonical)
        for source in (
            canonical,
            *entry.aliases,
            *automatic_pronunciation_forms(canonical),
            *entry.mistakes,
        ):
            source = _clean(source)
            key = (source.casefold(), canonical.casefold())
            if source and key not in seen:
                rules.append((source, canonical))
                seen.add(key)
    rules.sort(key=lambda item: len(item[0]), reverse=True)

    corrected = compact_cjk_spaces(text)
    changes: list[tuple[str, str]] = []
    for source, target in rules:
        pattern = _replacement_pattern(source)
        updated, count = pattern.subn(target, corrected)
        if count and updated != corrected:
            changes.append((source, target))
            corrected = updated
    return corrected, changes


def migrate_legacy(terms_path: Path, corrections_path: Path) -> list[GlossaryEntry]:
    terms: list[str] = []
    seen: set[str] = set()
    if terms_path.exists():
        for line in terms_path.read_text(encoding="utf-8").splitlines():
            value = _clean(line)
            key = value.casefold()
            if value and not value.startswith("#") and key not in seen:
                terms.append(value)
                seen.add(key)

    mistakes_by_target: dict[str, list[str]] = {}
    if corrections_path.exists():
        for line in corrections_path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            columns = line.split("\t")
            if len(columns) != 2:
                continue
            source, target = map(_clean, columns)
            if source and target:
                mistakes_by_target.setdefault(target.casefold(), []).append(source)

    return [
        GlossaryEntry(term, (), tuple(mistakes_by_target.get(term.casefold(), [])))
        for term in terms
    ]


def add_variant(
    entries: list[GlossaryEntry],
    canonical: str,
    value: str,
    *,
    kind: str,
) -> list[GlossaryEntry]:
    """Add an observed alias/mistake without discarding existing user data."""
    canonical = _clean(canonical)
    value = _clean(value)
    if not canonical or not value or kind not in {"alias", "mistake"}:
        return entries
    updated = list(entries)
    index = next(
        (position for position, entry in enumerate(updated)
         if entry.canonical.casefold() == canonical.casefold()),
        None,
    )
    if index is None:
        updated.append(GlossaryEntry(canonical))
        index = len(updated) - 1
    entry = updated[index]
    if value.casefold() == entry.canonical.casefold():
        return updated
    if kind == "alias":
        aliases = split_variants("、".join((*entry.aliases, value)))
        updated[index] = GlossaryEntry(entry.canonical, aliases, entry.mistakes)
    else:
        mistakes = split_variants("、".join((*entry.mistakes, value)))
        updated[index] = GlossaryEntry(entry.canonical, entry.aliases, mistakes)
    return updated


def _assignment(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("格式应为：标准写法=别名或错词")
    canonical, variant = value.split("=", 1)
    if not _clean(canonical) or not _clean(variant):
        raise argparse.ArgumentTypeError("等号两侧均不能为空")
    return canonical, variant


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移麦芽旧词库到结构化术语词典")
    parser.add_argument("--terms", type=Path, required=True)
    parser.add_argument("--corrections", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--add", action="append", default=[])
    parser.add_argument("--alias", action="append", type=_assignment, default=[])
    parser.add_argument("--mistake", action="append", type=_assignment, default=[])
    args = parser.parse_args()

    entries = load_glossary(args.output)
    if not entries:
        entries = migrate_legacy(args.terms, args.corrections)
    known = {entry.canonical.casefold() for entry in entries}
    for value in args.add:
        value = _clean(value)
        if value and value.casefold() not in known:
            entries.append(GlossaryEntry(value))
            known.add(value.casefold())
    for canonical, alias in args.alias:
        entries = add_variant(entries, canonical, alias, kind="alias")
    for canonical, mistake in args.mistake:
        entries = add_variant(entries, canonical, mistake, kind="mistake")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialize_glossary(entries), encoding="utf-8")
    print(f"已生成 {args.output}：{len(entries)} 个标准术语")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
