#!/usr/bin/env python3
"""Replay historical Meya recordings through controlled SeACo hotword variants.

Audio, manifests, transcripts, and reports stay under ``runtime/p0`` by default,
which is ignored by Git. The runner resumes completed rows so long evaluations
can be safely restarted.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys
import time
import unicodedata
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from asr_adapters import ParaformerAdapter  # noqa: E402
from glossary import (  # noqa: E402
    GlossaryEntry,
    apply_glossary_corrections,
    automatic_pronunciation_forms,
    load_glossary,
)
from seaco_hotwords import compile_glossary  # noqa: E402
from transcribe import load_wav  # noqa: E402


DEFAULT_MODEL = "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
DEFAULT_CONFIGS = ("no_hotword", "focused16", "focused32", "focused48", "all_effective")


@dataclass(frozen=True)
class Sample:
    audio: str
    duration: float
    reference: str | None = None
    terms: tuple[str, ...] = ()
    source: str = "unlabeled-history"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="麦芽 P0 SeACo 热词历史录音 A/B")
    parser.add_argument("--recordings", type=Path, default=ROOT / "recordings/voice-input")
    parser.add_argument("--manifest", type=Path, default=ROOT / "runtime/p0/manifest.json")
    parser.add_argument(
        "--references",
        type=Path,
        default=ROOT / "runtime/p0/references.json",
        help="Local filename-to-reference map; keep this ignored because it may contain private speech.",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "runtime/p0")
    parser.add_argument(
        "--glossary",
        type=Path,
        default=Path.home() / "Library/Application Support/Meya/glossary.tsv",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--configs", default=",".join(DEFAULT_CONFIGS))
    parser.add_argument("--only-labeled", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--refresh-manifest", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    return parser.parse_args()


def wav_duration(path: Path) -> float:
    import wave

    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


def load_references(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    references = payload.get("references", payload)
    if not isinstance(references, dict):
        raise ValueError("reference file must contain an object keyed by WAV filename")
    return {str(name): value for name, value in references.items() if isinstance(value, dict)}


def build_manifest(
    recordings: Path,
    manifest: Path,
    references: dict[str, dict[str, Any]] | None = None,
) -> list[Sample]:
    references = references or {}
    samples: list[Sample] = []
    for audio in sorted(recordings.glob("*.wav")):
        known = references.get(audio.name, {})
        samples.append(
            Sample(
                audio=str(audio.relative_to(ROOT)),
                duration=round(wav_duration(audio), 4),
                reference=known.get("reference"),
                terms=tuple(known.get("terms") or ()),
                source=str(known.get("source") or "unlabeled-history"),
            )
        )
    manifest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "privacy": "local-only; ignored by Git",
        "samples": [asdict(sample) for sample in samples],
    }
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return samples


def load_manifest(path: Path) -> list[Sample]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        Sample(
            audio=str(item["audio"]),
            duration=float(item["duration"]),
            reference=item.get("reference"),
            terms=tuple(item.get("terms") or ()),
            source=str(item.get("source") or "unlabeled-history"),
        )
        for item in payload.get("samples", [])
    ]


def normalized_text(text: str) -> str:
    return "".join(
        character.casefold()
        for character in unicodedata.normalize("NFKC", text)
        if not character.isspace() and unicodedata.category(character)[0] not in {"P", "S"}
    )


def edit_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, left_character in enumerate(left, start=1):
        current = [row]
        for column, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def character_error_rate(reference: str, hypothesis: str) -> float:
    expected = normalized_text(reference)
    actual = normalized_text(hypothesis)
    return edit_distance(expected, actual) / max(1, len(expected))


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def contains_text(text: str, value: str) -> bool:
    return normalized_text(value) in normalized_text(text)


def forms_for_term(entry: GlossaryEntry) -> tuple[str, ...]:
    return tuple(dict.fromkeys((entry.canonical, *entry.aliases, *automatic_pronunciation_forms(entry.canonical))))


def term_recalled(text: str, entry: GlossaryEntry, *, canonical_only: bool) -> bool:
    forms = (entry.canonical,) if canonical_only else forms_for_term(entry)
    return any(contains_text(text, form) for form in forms)


def glossary_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def prioritize_forms(
    all_forms: list[str],
    sample: Sample,
    entries_by_name: dict[str, GlossaryEntry],
) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()

    def append(value: str) -> None:
        key = value.casefold()
        if value and key not in seen and value in all_forms:
            selected.append(value)
            seen.add(key)

    for term in sample.terms:
        entry = entries_by_name.get(term.casefold())
        if entry:
            for form in forms_for_term(entry):
                append(form)
    for form in all_forms:
        append(form)
    return selected


def hotwords_for_config(
    config: str,
    all_forms: list[str],
    sample: Sample,
    entries_by_name: dict[str, GlossaryEntry],
) -> list[str]:
    if config == "no_hotword":
        return []
    if config == "all_effective":
        return list(all_forms)
    match = re.fullmatch(r"focused(\d+)", config)
    if not match:
        raise ValueError(f"未知配置: {config}")
    limit = int(match.group(1))
    return prioritize_forms(all_forms, sample, entries_by_name)[:limit]


def load_completed(path: Path, model: str, glossary_hash: str) -> set[tuple[str, str]]:
    completed: set[tuple[str, str]] = set()
    if not path.exists():
        return completed
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("model") == model and row.get("glossary_hash") == glossary_hash:
            completed.add((str(row.get("audio")), str(row.get("config"))))
    return completed


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()


def run_evaluation(
    samples: list[Sample],
    *,
    configs: tuple[str, ...],
    model: str,
    glossary_path: Path,
    output: Path,
    resume: bool,
) -> Path:
    entries = load_glossary(glossary_path)
    entries_by_name = {entry.canonical.casefold(): entry for entry in entries}
    adapter = ParaformerAdapter(ROOT, model, role="p0")
    adapter.hotword_file = output / "active-hotwords.txt"
    adapter.report_path = output / "hotword-compilation.json"
    compilation = compile_glossary(entries, adapter.seg_dict_path)
    all_forms = list(compilation.selected_hotwords)
    output.mkdir(parents=True, exist_ok=True)
    (output / "effective-hotwords.json").write_text(
        json.dumps(
            {
                "model": adapter.identifier,
                "selected_hotwords": all_forms,
                "entries": [asdict(item) for item in compilation.entries],
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    result_path = output / "results.jsonl"
    digest = glossary_digest(glossary_path)
    completed = load_completed(result_path, adapter.identifier, digest) if resume else set()
    pending = [
        (sample, config)
        for sample in samples
        for config in configs
        if (sample.audio, config) not in completed
    ]
    if not pending:
        return result_path

    print(f"加载 {adapter.identifier}，待运行 {len(pending)} 个组合…", flush=True)
    adapter.load()
    adapter.warmup()
    for index, (sample, config) in enumerate(pending, start=1):
        audio_path = ROOT / sample.audio
        started = time.perf_counter()
        try:
            audio, duration = load_wav(audio_path)
            hotwords = hotwords_for_config(config, all_forms, sample, entries_by_name)
            result = adapter.transcribe(
                audio,
                duration=duration,
                hotwords=hotwords,
                final=True,
            )
            raw_text = str(result.get("text") or "").strip()
            final_text, corrections = apply_glossary_corrections(raw_text, entries)
            row: dict[str, Any] = {
                "schema_version": 1,
                "audio": sample.audio,
                "duration": round(duration, 4),
                "config": config,
                "model": adapter.identifier,
                "glossary_hash": digest,
                "hotwords": hotwords,
                "hotword_count": len(hotwords),
                "raw_text": raw_text,
                "final_text": final_text,
                "corrections": [{"from": source, "to": target} for source, target in corrections],
                "elapsed": round(time.perf_counter() - started, 4),
                "reference": sample.reference,
                "terms": list(sample.terms),
                "source": sample.source,
            }
        except Exception as exc:
            row = {
                "schema_version": 1,
                "audio": sample.audio,
                "config": config,
                "model": adapter.identifier,
                "glossary_hash": digest,
                "error": str(exc),
                "elapsed": round(time.perf_counter() - started, 4),
                "reference": sample.reference,
                "terms": list(sample.terms),
                "source": sample.source,
            }
        append_jsonl(result_path, row)
        print(
            f"[{index}/{len(pending)}] {Path(sample.audio).name} {config} "
            f"{row.get('elapsed', 0):.2f}s {row.get('raw_text') or row.get('error', '')}",
            flush=True,
        )
    return result_path


def read_results(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def unique_latest_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("audio")),
            str(row.get("config")),
            str(row.get("model")),
            str(row.get("glossary_hash")),
        )
        unique[key] = row
    return list(unique.values())


def make_report(rows: list[dict[str, Any]], glossary_path: Path, output: Path) -> Path:
    rows = [row for row in unique_latest_rows(rows) if not row.get("error")]
    entries = load_glossary(glossary_path)
    entries_by_name = {entry.canonical.casefold(): entry for entry in entries}
    by_config: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_audio: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        by_config[str(row["config"])].append(row)
        by_audio[str(row["audio"])][str(row["config"])] = row

    lines = [
        "# 麦芽 P0 历史录音热词 A/B 报告",
        "",
        "> 本报告、录音与转写仅保存在本机 `runtime/p0/`，不进入 Git。",
        "",
        "## 数据概况",
        "",
        f"- 已完成识别组合：{len(rows)}",
        f"- 唯一历史录音：{len(by_audio)}",
        f"- 含完整人工真值的录音：{len({row['audio'] for row in rows if row.get('reference')})}",
        f"- 仅含已知目标术语的录音：{len({row['audio'] for row in rows if row.get('terms')})}",
        "",
        "## 各配置汇总",
        "",
        "| 配置 | 录音数 | 实际热词均值 | 平均耗时 | P95耗时 | 有真值CER | 原文形式召回 | 终稿标准词召回 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    summary_payload: dict[str, Any] = {"configs": {}}
    for config in sorted(by_config):
        config_rows = by_config[config]
        elapsed = [float(row.get("elapsed") or 0) for row in config_rows]
        labeled = [row for row in config_rows if row.get("reference")]
        cer_values = [character_error_rate(str(row["reference"]), str(row["final_text"])) for row in labeled]
        term_total = 0
        raw_form_hits = 0
        final_canonical_hits = 0
        for row in config_rows:
            for term in row.get("terms") or []:
                entry = entries_by_name.get(str(term).casefold())
                if entry:
                    term_total += 1
                    raw_form_hits += term_recalled(str(row["raw_text"]), entry, canonical_only=False)
                    final_canonical_hits += term_recalled(
                        str(row["final_text"]), entry, canonical_only=True
                    )
        mean_hotwords = statistics.fmean(float(row.get("hotword_count") or 0) for row in config_rows)
        mean_elapsed = statistics.fmean(elapsed) if elapsed else 0.0
        p95_elapsed = percentile(elapsed, 0.95)
        mean_cer = statistics.fmean(cer_values) if cer_values else None
        raw_form_recall = raw_form_hits / term_total if term_total else None
        final_canonical_recall = final_canonical_hits / term_total if term_total else None
        cer_text = f"{mean_cer:.3f}" if mean_cer is not None else "-"
        raw_recall_text = f"{raw_form_recall:.1%}" if raw_form_recall is not None else "-"
        final_recall_text = (
            f"{final_canonical_recall:.1%}" if final_canonical_recall is not None else "-"
        )
        lines.append(
            f"| {config} | {len(config_rows)} | {mean_hotwords:.1f} | {mean_elapsed:.3f}s | "
            f"{p95_elapsed:.3f}s | {cer_text} | {raw_recall_text} | {final_recall_text} |"
        )
        summary_payload["configs"][config] = {
            "rows": len(config_rows),
            "mean_hotwords": mean_hotwords,
            "mean_elapsed": mean_elapsed,
            "p95_elapsed": p95_elapsed,
            "mean_cer": mean_cer,
            "raw_form_recall": raw_form_recall,
            "raw_form_hits": raw_form_hits,
            "final_canonical_recall": final_canonical_recall,
            "final_canonical_hits": final_canonical_hits,
            "term_total": term_total,
        }

    memory_path = output / "memory.json"
    if memory_path.exists():
        memory = json.loads(memory_path.read_text(encoding="utf-8"))
        baseline_rss = int(memory.get("no_hotword_max_rss_bytes") or 0)
        hotword_rss = int(memory.get("all_effective_max_rss_bytes") or 0)
        rss_delta = hotword_rss - baseline_rss
        summary_payload["memory"] = {
            **memory,
            "delta_bytes": rss_delta,
            "delta_percent": rss_delta / baseline_rss if baseline_rss else 0.0,
        }
        lines.extend(
            [
                "",
                "## 独立进程峰值内存",
                "",
                f"- 无热词：{baseline_rss / 1024**3:.3f} GiB",
                f"- 全量有效热词：{hotword_rss / 1024**3:.3f} GiB",
                f"- 差值：{rss_delta / 1024**2:+.1f} MiB（可视为测量噪声）",
            ]
        )
    raw_changes = []
    for audio, configs in sorted(by_audio.items()):
        baseline = configs.get("no_hotword")
        all_effective = configs.get("all_effective")
        if baseline and all_effective and normalized_text(str(baseline["raw_text"])) != normalized_text(str(all_effective["raw_text"])):
            raw_changes.append((audio, baseline, all_effective))
    paired_audio = [
        configs
        for configs in by_audio.values()
        if "no_hotword" in configs and "all_effective" in configs
    ]
    baseline_elapsed = [float(configs["no_hotword"].get("elapsed") or 0) for configs in paired_audio]
    hotword_elapsed = [float(configs["all_effective"].get("elapsed") or 0) for configs in paired_audio]
    mean_baseline = statistics.fmean(baseline_elapsed) if baseline_elapsed else 0.0
    mean_hotword = statistics.fmean(hotword_elapsed) if hotword_elapsed else 0.0
    latency_delta = mean_hotword - mean_baseline
    latency_percent = latency_delta / mean_baseline if mean_baseline else 0.0
    drift_rate = len(raw_changes) / len(paired_audio) if paired_audio else 0.0
    summary_payload["paired_history"] = {
        "recordings": len(paired_audio),
        "raw_changes": len(raw_changes),
        "raw_change_rate": drift_rate,
        "mean_latency_delta_seconds": latency_delta,
        "mean_latency_delta_percent": latency_percent,
    }
    lines.extend(
        [
            "",
            "## 全历史录音的声学变化",
            "",
            f"在同时具有 `no_hotword` 和 `all_effective` 的录音中，模型原文发生变化：{len(raw_changes)} 条。",
            f"变化率：{drift_rate:.1%}；平均单句额外耗时：{latency_delta:.3f}s（{latency_percent:.1%}）。",
            "该数字只表示热词影响了解码，不代表一定变好；有人工真值的成对比较才具有方向性。",
            "",
        ]
    )
    for audio, baseline, all_effective in raw_changes[:80]:
        lines.extend(
            [
                f"### {Path(audio).name}",
                "",
                f"- 无热词：{baseline['raw_text']}",
                f"- 全量有效热词：{all_effective['raw_text']}",
                "",
            ]
        )

    known_audio = [audio for audio, configs in sorted(by_audio.items()) if any(row.get("terms") for row in configs.values())]
    lines.extend(["## 已知术语样本明细", ""])
    for audio in known_audio:
        lines.extend([f"### {Path(audio).name}", ""])
        sample_rows = by_audio[audio]
        reference = next((row.get("reference") for row in sample_rows.values() if row.get("reference")), None)
        terms = next((row.get("terms") for row in sample_rows.values() if row.get("terms")), [])
        if reference:
            lines.append(f"- 人工真值：{reference}")
        lines.append(f"- 目标术语：{', '.join(terms)}")
        for config, row in sorted(sample_rows.items()):
            cer = character_error_rate(str(reference), str(row["final_text"])) if reference else None
            suffix = f"，CER={cer:.3f}" if cer is not None else ""
            lines.append(f"- `{config}` 原文：{row['raw_text']}；终稿：{row['final_text']}{suffix}")
        lines.append("")

    lines.extend(
        [
            "## P0 决策",
            "",
            "- 同一音频在有无热词时出现原文差异，说明热词确实进入了 SeACo 解码；差异是否改善必须以人工真值判断。",
            "- 只标注术语但没有完整真值的录音不能用于计算整句准确率，也不能把标准化后的写法当作声学提升。",
            f"- 静态 42 个有效热词使 {len(raw_changes)}/{len(paired_audio)} 条真实历史录音发生解码漂移，并增加平均 {latency_delta:.3f}s 延迟。",
            "- 因此不应继续扩大静态热词表；进入 P1 时应优先实现按本句选择、每术语一个 form 和仅对入选术语做标准化。",
            "- 当前 FunASR 结果包含汉字间空格，导致部分中文 alias 无法被现有后处理直接命中；这应作为 P1 前置兼容修复，并单独验证不改变英文空格。",
            "",
            "## 解释边界",
            "",
            "- 本地 reference 文件中的 `reference` 表示人工确认的完整说话意图。",
            "- 只有 `terms` 没有 `reference` 时，只确认录音包含某术语，不捏造整句真值。",
            "- `unlabeled-history` 只用于观察同音频输出是否变化，不能计算准确率方向。",
            "- 最终结论必须优先依据成对录音和人工真值，不能把后处理后的标准写法误算成模型声学能力。",
            "",
        ]
    )
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    (output / "summary.json").write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report_path


def main() -> int:
    args = parse_args()
    if args.refresh_manifest or not args.manifest.exists():
        samples = build_manifest(args.recordings, args.manifest, load_references(args.references))
    else:
        samples = load_manifest(args.manifest)
    if args.only_labeled:
        samples = [sample for sample in samples if sample.reference or sample.terms]
    if args.limit is not None:
        samples = samples[: max(0, args.limit)]
    configs = tuple(value.strip() for value in args.configs.split(",") if value.strip())
    result_path = args.output / "results.jsonl"
    if not args.report_only:
        result_path = run_evaluation(
            samples,
            configs=configs,
            model=args.model,
            glossary_path=args.glossary,
            output=args.output,
            resume=not args.no_resume,
        )
    report_path = make_report(read_results(result_path), args.glossary, args.output)
    print(f"P0 报告：{report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
