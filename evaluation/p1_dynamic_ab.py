#!/usr/bin/env python3
"""Replay P0 history with utterance-scoped dynamic SeACo hotwords."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from asr_adapters import ParaformerAdapter  # noqa: E402
from glossary import apply_glossary_corrections, load_glossary  # noqa: E402
from hotword_selector import select_hotword_entries  # noqa: E402
from evaluation.p0_hotword_ab import (  # noqa: E402
    DEFAULT_MODEL,
    Sample,
    character_error_rate,
    load_manifest,
    normalized_text,
    percentile,
    read_results,
    unique_latest_rows,
)
from transcribe import load_wav  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="麦芽 P1 动态热词历史录音 A/B")
    parser.add_argument("--manifest", type=Path, default=ROOT / "runtime/p0/manifest.json")
    parser.add_argument("--baseline", type=Path, default=ROOT / "runtime/p0/results.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "runtime/p1")
    parser.add_argument(
        "--glossary",
        type=Path,
        default=Path.home() / "Library/Application Support/Meya/glossary.tsv",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--configs", default="context_only,matching_app")
    parser.add_argument("--limit", type=int, default=16)
    parser.add_argument("--sample-limit", type=int)
    parser.add_argument("--only-labeled", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    return parser.parse_args()


def baseline_rows(path: Path) -> dict[str, dict[str, Any]]:
    return {
        str(row["audio"]): row
        for row in unique_latest_rows(read_results(path))
        if row.get("config") == "no_hotword" and not row.get("error")
    }


def append_result(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()


def run(
    samples: list[Sample],
    *,
    configs: tuple[str, ...],
    baseline: dict[str, dict[str, Any]],
    glossary_path: Path,
    model: str,
    output: Path,
    limit: int,
    resume: bool,
) -> Path:
    entries = load_glossary(glossary_path)
    result_path = output / "results.jsonl"
    completed = {
        (str(row.get("audio")), str(row.get("config")))
        for row in read_results(result_path)
    } if resume else set()
    pending = [
        (sample, config)
        for sample in samples
        for config in configs
        if sample.audio in baseline and (sample.audio, config) not in completed
    ]
    if not pending:
        return result_path
    adapter = ParaformerAdapter(ROOT, model, role="p1")
    adapter.hotword_file = output / "active-hotwords.txt"
    adapter.report_path = output / "hotword-compilation.json"
    adapter.load()
    adapter.warmup()
    for index, (sample, config) in enumerate(pending, start=1):
        source = baseline[sample.audio]
        draft = str(source.get("raw_text") or "")
        app_name = "DevPilot" if config == "matching_app" else ""
        selection = select_hotword_entries(
            entries,
            draft_text=draft,
            app_name=app_name,
            limit=limit,
        )
        hotwords = adapter.prepare_hotwords(
            list(selection.acoustic_entries),
            max_terms=limit,
            max_forms_per_entry=1,
        ) if selection.acoustic_entries else []
        started = time.perf_counter()
        try:
            audio, duration = load_wav(ROOT / sample.audio)
            result = adapter.transcribe(audio, duration=duration, hotwords=hotwords, final=True)
            raw_text = str(result.get("text") or "").strip()
            evidenced = select_hotword_entries(entries, draft_text=raw_text, limit=limit)
            active = {entry.canonical.casefold(): entry for entry in selection.entries}
            for entry in evidenced.entries:
                active.setdefault(entry.canonical.casefold(), entry)
            final_text, corrections = apply_glossary_corrections(raw_text, list(active.values()))
            row = {
                "schema_version": 1,
                "audio": sample.audio,
                "config": config,
                "model": adapter.identifier,
                "duration": duration,
                "elapsed": round(time.perf_counter() - started, 4),
                "baseline_raw_text": draft,
                "raw_text": raw_text,
                "final_text": final_text,
                "selected_terms": [entry.canonical for entry in selection.entries],
                "hotwords": hotwords,
                "corrections": [{"from": a, "to": b} for a, b in corrections],
                "reference": sample.reference,
                "terms": list(sample.terms),
            }
        except Exception as exc:
            row = {
                "schema_version": 1,
                "audio": sample.audio,
                "config": config,
                "error": str(exc),
                "elapsed": round(time.perf_counter() - started, 4),
            }
        append_result(result_path, row)
        print(
            f"[{index}/{len(pending)}] {Path(sample.audio).name} {config} "
            f"terms={len(selection.entries)} {row.get('elapsed', 0):.2f}s",
            flush=True,
        )
    return result_path


def report(rows: list[dict[str, Any]], output: Path) -> Path:
    rows = [row for row in unique_latest_rows(rows) if not row.get("error")]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["config"])].append(row)
    lines = [
        "# 麦芽 P1 动态热词历史回放",
        "",
        "> `context_only` 只使用无热词草稿模拟实时识别上下文；`matching_app` 额外模拟当前应用与一个虚构术语同名。",
        "",
        "| 配置 | 录音 | 平均入选词 | 零热词占比 | 原文漂移 | 平均耗时 | P95 | 真值 CER |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    summary: dict[str, Any] = {"configs": {}}
    for config in sorted(grouped):
        values = grouped[config]
        counts = [len(row.get("hotwords") or []) for row in values]
        elapsed = [float(row.get("elapsed") or 0) for row in values]
        drift = sum(
            normalized_text(str(row.get("baseline_raw_text") or ""))
            != normalized_text(str(row.get("raw_text") or ""))
            for row in values
        )
        labeled = [row for row in values if row.get("reference")]
        cers = [
            character_error_rate(str(row["reference"]), str(row["final_text"]))
            for row in labeled
        ]
        metrics = {
            "rows": len(values),
            "mean_hotwords": statistics.fmean(counts) if counts else 0.0,
            "zero_hotword_rate": sum(count == 0 for count in counts) / len(counts) if counts else 0.0,
            "raw_drift_rate": drift / len(values) if values else 0.0,
            "raw_drift_count": drift,
            "mean_elapsed": statistics.fmean(elapsed) if elapsed else 0.0,
            "p95_elapsed": percentile(elapsed, 0.95),
            "mean_cer": statistics.fmean(cers) if cers else None,
        }
        summary["configs"][config] = metrics
        cer = f"{metrics['mean_cer']:.3f}" if metrics["mean_cer"] is not None else "-"
        lines.append(
            f"| {config} | {metrics['rows']} | {metrics['mean_hotwords']:.2f} | "
            f"{metrics['zero_hotword_rate']:.1%} | {metrics['raw_drift_rate']:.1%} | "
            f"{metrics['mean_elapsed']:.3f}s | {metrics['p95_elapsed']:.3f}s | {cer} |"
        )
    lines.extend(["", "## 漂移明细", ""])
    for row in rows:
        if normalized_text(str(row.get("baseline_raw_text") or "")) == normalized_text(str(row.get("raw_text") or "")):
            continue
        lines.extend(
            [
                f"### {Path(str(row['audio'])).name} · {row['config']}",
                "",
                f"- 入选：{', '.join(row.get('selected_terms') or []) or '无'}",
                f"- 基线：{row.get('baseline_raw_text', '')}",
                f"- 动态：{row.get('raw_text', '')}",
                f"- 终稿：{row.get('final_text', '')}",
                "",
            ]
        )
    output.mkdir(parents=True, exist_ok=True)
    path = output / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def main() -> int:
    args = parse_args()
    samples = load_manifest(args.manifest)
    if args.only_labeled:
        samples = [sample for sample in samples if sample.reference or sample.terms]
    if args.sample_limit is not None:
        samples = samples[: max(0, args.sample_limit)]
    result_path = args.output / "results.jsonl"
    if not args.report_only:
        result_path = run(
            samples,
            configs=tuple(value.strip() for value in args.configs.split(",") if value.strip()),
            baseline=baseline_rows(args.baseline),
            glossary_path=args.glossary,
            model=args.model,
            output=args.output,
            limit=max(0, min(24, args.limit)),
            resume=not args.no_resume,
        )
    path = report(read_results(result_path), args.output)
    print(f"P1 报告：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
