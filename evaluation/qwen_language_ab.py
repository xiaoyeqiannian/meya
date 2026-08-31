#!/usr/bin/env python3
"""Compare Qwen3-ASR forced-Chinese and automatic language modes on Meya history."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.final_model_compare import (
    ROOT,
    engine_metrics,
    english_term_hit,
    english_terms,
    latest_rows,
    load_alignment,
    load_references,
    load_run,
    mer,
    optional,
    read_jsonl,
)


FORCED_ID = "qwen3-1.7b"
AUTO_ID = "qwen3-1.7b-auto"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Qwen3-ASR 自动语言/强制中文 A/B")
    parser.add_argument(
        "--forced-results",
        type=Path,
        default=ROOT / "runtime/final-model-compare/results.jsonl",
    )
    parser.add_argument(
        "--auto-results",
        type=Path,
        default=ROOT / "runtime/qwen-language-ab/results.jsonl",
    )
    parser.add_argument(
        "--auto-run",
        type=Path,
        default=ROOT / "runtime/qwen-language-ab/run-qwen3-1.7b-auto.json",
    )
    parser.add_argument(
        "--alignment",
        type=Path,
        default=ROOT / "runtime/history-compare/codex-alignment.json",
    )
    parser.add_argument(
        "--references",
        type=Path,
        default=ROOT / "runtime/p0/references.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "runtime/qwen-language-ab",
    )
    parser.add_argument(
        "--candidate-limit",
        type=int,
        default=200,
        help="Maximum weak-label samples queued for human review before fine-tuning",
    )
    return parser.parse_args()


def rows_for(path: Path, engine: str) -> list[dict[str, Any]]:
    latest = latest_rows(read_jsonl(path))
    return [row for (_audio, value), row in latest.items() if value == engine]


def term_hit(text: str, term: str) -> bool:
    return english_term_hit(text, term)


def make_report(args: argparse.Namespace) -> Path:
    forced_rows = rows_for(args.forced_results, FORCED_ID)
    auto_rows = rows_for(args.auto_results, AUTO_ID)
    aligned = load_alignment(args.alignment)
    references = load_references(args.references)
    forced_by_audio = {str(row["audio"]): row for row in forced_rows if not row.get("error")}
    auto_by_audio = {str(row["audio"]): row for row in auto_rows if not row.get("error")}
    all_shared_audio = sorted(set(forced_by_audio) & set(auto_by_audio))
    raw_different = sum(
        str(forced_by_audio[audio].get("final_text") or "")
        != str(auto_by_audio[audio].get("final_text") or "")
        for audio in all_shared_audio
    )
    shared = [
        item for item in aligned
        if str(item.get("audio")) in forced_by_audio and str(item.get("audio")) in auto_by_audio
    ]

    metrics = {
        "forced_chinese": engine_metrics(FORCED_ID, forced_rows, aligned, references, {}),
        "auto": engine_metrics(AUTO_ID, auto_rows, aligned, references, load_run(args.auto_run)),
    }
    pairwise = {"auto_better": 0, "forced_better": 0, "tie": 0}
    recovered: list[dict[str, str]] = []
    lost: list[dict[str, str]] = []
    examples: list[tuple[float, dict[str, str]]] = []
    candidates: list[dict[str, Any]] = []
    for item in shared:
        audio = str(item["audio"])
        reference = str(item.get("accepted_text") or "")
        forced = str(forced_by_audio[audio].get("final_text") or "")
        auto = str(auto_by_audio[audio].get("final_text") or "")
        forced_mer = mer(reference, forced)
        auto_mer = mer(reference, auto)
        if auto_mer < forced_mer:
            pairwise["auto_better"] += 1
        elif forced_mer < auto_mer:
            pairwise["forced_better"] += 1
        else:
            pairwise["tie"] += 1
        examples.append((abs(forced_mer - auto_mer), {
            "audio": audio,
            "reference": reference,
            "forced": forced,
            "auto": auto,
        }))
        terms = english_terms(reference)
        missed_terms: list[str] = []
        for term in terms:
            forced_hit = term_hit(forced, term)
            auto_hit = term_hit(auto, term)
            record = {"audio": audio, "term": term, "forced": forced, "auto": auto}
            if auto_hit and not forced_hit:
                recovered.append(record)
            elif forced_hit and not auto_hit:
                lost.append(record)
            if not auto_hit:
                missed_terms.append(term)
        if auto_mer >= 0.15 or missed_terms:
            candidates.append({
                "audio": audio,
                "weak_reference": reference,
                "hypothesis": auto,
                "mer": round(auto_mer, 6),
                "english_terms": terms,
                "missed_terms": missed_terms,
                "priority_score": round(auto_mer + min(len(missed_terms), 3) * 0.25, 6),
                "review_status": "pending",
                "label_warning": "weak_reference_must_be_verified_against_audio",
            })

    args.output.mkdir(parents=True, exist_ok=True)
    selected_candidates = sorted(
        candidates,
        key=lambda value: (value["priority_score"], value["mer"]),
        reverse=True,
    )[: max(args.candidate_limit, 0)]
    summary = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "interpretation": "same MLX 4-bit model and audio; only language constraint differs",
        "aligned_rows": len(shared),
        "all_shared_rows": len(all_shared_audio),
        "raw_transcription_different": raw_different,
        "raw_transcription_exact_same": len(all_shared_audio) - raw_different,
        "metrics": metrics,
        "pairwise_mer": pairwise,
        "english_terms_recovered": len(recovered),
        "english_terms_lost": len(lost),
        "fine_tune_candidates": len(selected_candidates),
        "recovered": recovered,
        "lost": lost,
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (args.output / "fine-tune-candidates.jsonl").open("w", encoding="utf-8") as handle:
        for candidate in selected_candidates:
            handle.write(json.dumps(candidate, ensure_ascii=False) + "\n")

    lines = [
        "# Qwen3-ASR-1.7B：自动语言 vs 强制中文",
        "",
        "> 同一个 MLX 4bit 模型、同一批历史音频，只改变语言约束。",
        "> 已发送文本是高置信弱标签，不等同于逐条人工听写真值。",
        "",
        "## 汇总",
        "",
        "| 模式 | 全部 CER ↓ | 全部 MER ↓ | 技术 MER ↓ | 英文术语命中 ↑ | 平均耗时 | P95 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key, label in (("forced_chinese", "强制中文"), ("auto", "自动语言")):
        value = metrics[key]
        recall = value.get("english_term_recall")
        recall_text = "-" if recall is None else (
            f"{value['english_term_hits']}/{value['english_term_total']} ({recall:.1%})"
        )
        lines.append(
            f"| {label} | {optional(value.get('weak_cer'))} | {optional(value.get('weak_mer'))} | "
            f"{optional(value.get('technical_mer'))} | {recall_text} | "
            f"{optional(value.get('mean_elapsed'), suffix='s')} | {optional(value.get('p95_elapsed'), suffix='s')} |"
        )
    lines.extend([
        "",
        "## 成对比较",
        "",
        f"- 自动语言更好：{pairwise['auto_better']} 条",
        f"- 强制中文更好：{pairwise['forced_better']} 条",
        f"- 相同：{pairwise['tie']} 条",
        f"- 全量 {len(all_shared_audio)} 条中转写完全一致：{len(all_shared_audio) - raw_different} 条；有任何字面差异：{raw_different} 条",
        f"- 自动语言新增命中的英文术语：{len(recovered)} 个",
        f"- 自动语言丢失的英文术语：{len(lost)} 个",
        f"- 待人工听写确认的微调候选：{len(selected_candidates)} 条",
        "",
        "## 高分歧样本",
        "",
    ])
    differing_examples = [value for value in examples if value[0] > 0]
    if not differing_examples:
        lines.extend(["高置信对齐样本中，两种模式没有 MER 差异。", ""])
    for _delta, item in sorted(
        differing_examples, key=lambda value: value[0], reverse=True
    )[:30]:
        lines.extend([
            f"### {item['audio']}",
            "",
            f"- 已发送：{item['reference']}",
            f"- 强制中文：{item['forced']}",
            f"- 自动语言：{item['auto']}",
            "",
        ])
    report = args.output / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(report)
    return report


def main() -> int:
    make_report(parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
