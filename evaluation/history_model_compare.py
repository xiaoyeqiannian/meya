#!/usr/bin/env python3
"""Replay all local Meya recordings through independent and production ASR paths.

Private audio and transcripts are written only below ``runtime/history-compare``.
The independent Whisper output is a comparison baseline, not fabricated truth;
only explicitly labeled recordings contribute to accuracy claims.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import time
from typing import Any, Iterable
import unicodedata

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from asr_adapters import ParaformerAdapter  # noqa: E402
from glossary import apply_glossary_corrections, compact_cjk_spaces, load_glossary  # noqa: E402
from hotword_selector import select_hotword_entries  # noqa: E402
from transcribe import load_wav  # noqa: E402


DEFAULT_WHISPER = "mlx-community/whisper-large-v3-mlx"
DEFAULT_PREVIEW = "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
DEFAULT_FINAL = "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
ENGINES = ("whisper", "preview", "final")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="麦芽全历史录音模型对比")
    parser.add_argument("--engine", choices=(*ENGINES, "report"), required=True)
    parser.add_argument("--recordings", type=Path, default=ROOT / "recordings/voice-input")
    parser.add_argument("--output", type=Path, default=ROOT / "runtime/history-compare")
    parser.add_argument(
        "--references",
        type=Path,
        default=ROOT / "runtime/p0/references.json",
    )
    parser.add_argument(
        "--glossary",
        type=Path,
        default=Path.home() / "Library/Application Support/Meya/glossary.tsv",
    )
    parser.add_argument("--whisper-model", default=DEFAULT_WHISPER)
    parser.add_argument("--preview-model", default=DEFAULT_PREVIEW)
    parser.add_argument("--final-model", default=DEFAULT_FINAL)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--final-with-preview",
        action="store_true",
        help="Use a completed preview draft for dynamic pre-decode hotwords.",
    )
    parser.add_argument("--no-resume", action="store_true")
    return parser.parse_args()


def load_references(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("references", payload)
    return {
        str(name): value
        for name, value in values.items()
        if isinstance(value, dict)
    } if isinstance(values, dict) else {}


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def latest_rows(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        output[(str(row.get("audio")), str(row.get("engine")))] = row
    return output


def append_row(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()


def normalize(text: str) -> str:
    return "".join(
        character.casefold()
        for character in unicodedata.normalize("NFKC", text or "")
        if not character.isspace() and unicodedata.category(character)[0] not in {"P", "S"}
    )


def edit_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, left_character in enumerate(left, start=1):
        current = [row]
        for column, right_character in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (left_character != right_character),
            ))
        previous = current
    return previous[-1]


def cer(reference: str, hypothesis: str) -> float:
    expected = normalize(reference)
    return edit_distance(expected, normalize(hypothesis)) / max(1, len(expected))


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def has_foreign_script(text: str) -> bool:
    ranges = (
        ("\u3040", "\u30ff"),
        ("\u0400", "\u052f"),
        ("\u0600", "\u06ff"),
        ("\u0900", "\u097f"),
        ("\u0e00", "\u0e7f"),
        ("\uac00", "\ud7af"),
    )
    return any(start <= character <= end for character in text for start, end in ranges)


def audio_metrics(audio: np.ndarray) -> tuple[float, float]:
    if not len(audio):
        return 0.0, 0.0
    rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
    peak = float(np.max(np.abs(audio)))
    return rms, peak


def load_recent_terms_read_only(user_data_dir: Path) -> dict[str, float]:
    """Read production term weights without migrating or mutating user state."""
    path = user_data_dir / "learning.sqlite3"
    if not path.exists():
        return {}
    current = datetime.now(timezone.utc)
    output: dict[str, float] = {}
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT canonical, accepted_count, last_accepted_at
            FROM term_usage WHERE retired_at IS NULL
            """
        ).fetchall()
        connection.close()
    except sqlite3.Error:
        return {}
    for row in rows:
        try:
            accepted = max(0, int(row["accepted_count"]))
            last = datetime.fromisoformat(str(row["last_accepted_at"]).replace("Z", "+00:00"))
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        age_days = max(0.0, (current - last).total_seconds() / 86_400.0)
        if age_days < 90:
            weight = accepted * math.pow(0.5, age_days / 30.0)
            if weight >= 0.25:
                output[str(row["canonical"])] = weight
    return output


def base_row(audio_path: Path, engine: str, model: str, duration: float) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "audio": audio_path.name,
        "engine": engine,
        "model": model,
        "duration": round(duration, 4),
    }


def run_whisper(
    recordings: list[Path],
    *,
    model: str,
    result_path: Path,
    completed: set[tuple[str, str]],
) -> None:
    os.environ["HF_HOME"] = str(ROOT / "models/huggingface")
    os.environ["HF_HUB_CACHE"] = str(ROOT / "models/huggingface/hub")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    import mlx_whisper

    pending = [path for path in recordings if (path.name, "whisper") not in completed]
    for index, audio_path in enumerate(pending, start=1):
        started = time.perf_counter()
        try:
            audio, duration = load_wav(audio_path)
            rms, peak = audio_metrics(audio)
            if rms < 0.008 and peak < 0.05:
                text = ""
                silence = True
            else:
                result = mlx_whisper.transcribe(
                    audio,
                    path_or_hf_repo=model,
                    task="transcribe",
                    language="zh",
                    temperature=0.0,
                    condition_on_previous_text=True,
                    verbose=False,
                )
                text = str(result.get("text") or "").strip()
                silence = False
            row = {
                **base_row(audio_path, "whisper", model, duration),
                "raw_text": text,
                "final_text": text,
                "silence": silence,
                "rms": round(rms, 6),
                "peak": round(peak, 6),
                "elapsed": round(time.perf_counter() - started, 4),
            }
        except Exception as error:
            row = {
                **base_row(audio_path, "whisper", model, 0.0),
                "error": str(error),
                "elapsed": round(time.perf_counter() - started, 4),
            }
        append_row(result_path, row)
        print(
            f"[{index}/{len(pending)}] whisper {audio_path.name} "
            f"{row['elapsed']:.2f}s {row.get('raw_text') or row.get('error') or '[silence]'}",
            flush=True,
        )


def run_paraformer(
    recordings: list[Path],
    *,
    engine: str,
    model: str,
    result_path: Path,
    existing: dict[tuple[str, str], dict[str, Any]],
    glossary_path: Path,
    final_with_preview: bool = False,
) -> None:
    pending = [path for path in recordings if (path.name, engine) not in existing]
    if not pending:
        return
    if engine == "final" and final_with_preview:
        missing = [path.name for path in pending if (path.name, "preview") not in existing]
        if missing:
            raise RuntimeError(f"终稿评测需要先完成实时识别，尚缺 {len(missing)} 条")
    entries = load_glossary(glossary_path)
    recent_terms = load_recent_terms_read_only(glossary_path.parent)
    adapter = ParaformerAdapter(ROOT, model, role=f"history-{engine}")
    adapter.load()
    adapter.warmup()
    for index, audio_path in enumerate(pending, start=1):
        started = time.perf_counter()
        try:
            audio, duration = load_wav(audio_path)
            rms, peak = audio_metrics(audio)
            silence = rms < 0.008 and peak < 0.05
            selected_terms: list[str] = []
            hotwords: list[str] = []
            if silence:
                raw_text = ""
                final_text = ""
                corrections: list[tuple[str, str]] = []
            else:
                selection = None
                if engine == "final" and final_with_preview:
                    draft = str(existing[(audio_path.name, "preview")].get("raw_text") or "")
                    selection = select_hotword_entries(
                        entries,
                        draft_text=draft,
                        recent_terms=recent_terms,
                        limit=16,
                    )
                    selected_terms = [entry.canonical for entry in selection.entries]
                    if selection.acoustic_entries:
                        hotwords = adapter.prepare_hotwords(
                            list(selection.acoustic_entries),
                            max_terms=16,
                            max_forms_per_entry=1,
                        )
                result = adapter.transcribe(
                    audio,
                    duration=duration,
                    hotwords=hotwords,
                    # The online preview worker never owns the final commit.
                    # Replaying it with is_final=True exercises a different,
                    # slow path and exaggerates repetition, so capture the
                    # last non-final stream hypothesis used by the UI instead.
                    final=engine == "final",
                    revision=1 if engine == "preview" else None,
                )
                raw_text = compact_cjk_spaces(str(result.get("text") or "").strip())
                if engine == "preview":
                    final_text = raw_text
                    corrections = []
                else:
                    evidenced = select_hotword_entries(entries, draft_text=raw_text, limit=16)
                    active = {
                        entry.canonical.casefold(): entry
                        for entry in (selection.entries if selection else ())
                    }
                    for entry in evidenced.entries:
                        active.setdefault(entry.canonical.casefold(), entry)
                    final_text, corrections = apply_glossary_corrections(raw_text, list(active.values()))
            row = {
                **base_row(audio_path, engine, adapter.identifier, duration),
                "raw_text": raw_text,
                "final_text": final_text,
                "silence": silence,
                "rms": round(rms, 6),
                "peak": round(peak, 6),
                "selected_terms": selected_terms,
                "hotwords": hotwords,
                "corrections": [
                    {"from": source, "to": target} for source, target in corrections
                ],
                "selection_mode": "preview_dynamic" if final_with_preview else "output_evidenced_only",
                "elapsed": round(time.perf_counter() - started, 4),
            }
        except Exception as error:
            row = {
                **base_row(audio_path, engine, adapter.identifier, 0.0),
                "error": str(error),
                "elapsed": round(time.perf_counter() - started, 4),
            }
        append_row(result_path, row)
        existing[(audio_path.name, engine)] = row
        print(
            f"[{index}/{len(pending)}] {engine} {audio_path.name} "
            f"{row['elapsed']:.2f}s {row.get('final_text') or row.get('error') or '[silence]'}",
            flush=True,
        )


def engine_metrics(rows: list[dict[str, Any]], references: dict[str, dict[str, Any]]) -> dict[str, Any]:
    successful = [row for row in rows if not row.get("error")]
    elapsed = [float(row.get("elapsed") or 0) for row in successful]
    real_time = [
        float(row.get("elapsed") or 0) / float(row.get("duration") or 1)
        for row in successful if float(row.get("duration") or 0) > 0
    ]
    labeled = [row for row in successful if references.get(str(row["audio"]), {}).get("reference")]
    cers = [
        cer(str(references[str(row["audio"])]["reference"]), str(row.get("final_text") or ""))
        for row in labeled
    ]
    term_total = 0
    term_hits = 0
    for row in successful:
        for term in references.get(str(row["audio"]), {}).get("terms") or []:
            term_total += 1
            term_hits += normalize(str(term)) in normalize(str(row.get("final_text") or ""))
    return {
        "rows": len(successful),
        "errors": len(rows) - len(successful),
        "mean_elapsed": statistics.fmean(elapsed) if elapsed else None,
        "p95_elapsed": percentile(elapsed, 0.95),
        "mean_real_time_factor": statistics.fmean(real_time) if real_time else None,
        "foreign_script_rows": sum(has_foreign_script(str(row.get("final_text") or "")) for row in successful),
        "labeled_rows": len(labeled),
        "mean_labeled_cer": statistics.fmean(cers) if cers else None,
        "term_hits": term_hits,
        "term_total": term_total,
        "term_recall": term_hits / term_total if term_total else None,
    }


def format_optional(value: float | None, suffix: str = "", digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}{suffix}"


def make_report(result_path: Path, output: Path, references_path: Path) -> Path:
    references = load_references(references_path)
    all_rows = latest_rows(read_rows(result_path))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_audio: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for (audio, engine), row in all_rows.items():
        grouped[engine].append(row)
        by_audio[audio][engine] = row
    metrics = {engine: engine_metrics(grouped.get(engine, []), references) for engine in ENGINES}

    complete = [values for values in by_audio.values() if "whisper" in values and "final" in values]
    comparisons: list[dict[str, Any]] = []
    preview_wins = final_wins = ties = 0
    identical_final = 0
    for values in complete:
        whisper = str(values["whisper"].get("final_text") or "")
        preview_row = values.get("preview")
        preview = str(preview_row.get("final_text") or "") if preview_row else ""
        final = str(values["final"].get("final_text") or "")
        preview_distance = cer(whisper, preview) if preview_row else None
        final_distance = cer(whisper, final)
        if normalize(whisper) == normalize(final):
            identical_final += 1
        if preview_distance is not None:
            if final_distance < preview_distance:
                final_wins += 1
            elif preview_distance < final_distance:
                preview_wins += 1
            else:
                ties += 1
        comparisons.append({
            "audio": str(values["final"]["audio"]),
            "whisper": whisper,
            "preview": preview,
            "final": final,
            "preview_vs_whisper_cer": preview_distance,
            "final_vs_whisper_cer": final_distance,
            "delta": final_distance - preview_distance if preview_distance is not None else None,
            "selected_terms": values["final"].get("selected_terms") or [],
            "corrections": values["final"].get("corrections") or [],
        })
    preview_comparisons = [
        row["preview_vs_whisper_cer"]
        for row in comparisons if row["preview_vs_whisper_cer"] is not None
    ]
    mean_preview_distance = statistics.fmean(preview_comparisons) if preview_comparisons else None
    mean_final_distance = statistics.fmean(
        row["final_vs_whisper_cer"] for row in comparisons
    ) if comparisons else None

    summary = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "interpretation": {
            "whisper_role": "independent local comparison baseline, not ground truth",
            "accuracy_claims": "human-confirmed references only",
        },
        "engines": metrics,
        "pairwise": {
            "complete_recordings": len(complete),
            "whisper_final_identical": identical_final,
            "whisper_final_identical_rate": identical_final / len(complete) if complete else None,
            "mean_preview_vs_whisper_cer": mean_preview_distance,
            "preview_comparison_rows": len(preview_comparisons),
            "mean_final_vs_whisper_cer": mean_final_distance,
            "final_closer": final_wins,
            "preview_closer": preview_wins,
            "tie": ties,
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# 麦芽全历史录音模型对比",
        "",
        "> 转写与报告只在本机 `runtime/history-compare/`，不进入 Git。",
        "> Whisper large-v3 是 Codex 组织的独立对照模型，不是人工真值。只有已人工确认样本可用于准确率结论。",
        "",
        "## 覆盖和真值指标",
        "",
        "| 路径 | 成功录音 | 失败 | 平均耗时 | P95 | 实时率 | 异文字系 | 人工真值 CER | 专名命中 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    labels = {"whisper": "Codex/独立 Whisper", "preview": "麦芽实时", "final": "麦芽终稿"}
    for engine in ENGINES:
        value = metrics[engine]
        term_recall = value["term_recall"]
        term_text = "-" if term_recall is None else f"{value['term_hits']}/{value['term_total']} ({term_recall:.1%})"
        lines.append(
            f"| {labels[engine]} | {value['rows']} | {value['errors']} | "
            f"{format_optional(value['mean_elapsed'], 's')} | {value['p95_elapsed']:.3f}s | "
            f"{format_optional(value['mean_real_time_factor'], 'x')} | {value['foreign_script_rows']} | "
            f"{format_optional(value['mean_labeled_cer'])} | {term_text} |"
        )
    lines.extend([
        "",
        "## 全量模型差异（非真值）",
        "",
        f"- 独立 Whisper 与麦芽终稿成对覆盖：{len(complete)} 条。",
        f"- 麦芽终稿与独立 Whisper 完全一致：{identical_final}/{len(complete)}" if complete else "- 暂无完整对比。",
        f"- 麦芽实时相对 Whisper 的平均差异 CER：{format_optional(mean_preview_distance)}（{len(preview_comparisons)} 条抽样）。",
        f"- 麦芽终稿相对 Whisper 的平均差异 CER：{format_optional(mean_final_distance)}。",
        f"- 在有实时抽样的录音中，终稿比实时更接近 Whisper {final_wins} 条，更远 {preview_wins} 条，持平 {ties} 条。",
        "- 这些数字只用于定位模型分歧；两个模型也可能同时错。",
        "",
        "## 优先人工复核的分歧样本",
        "",
    ])
    disagreements = sorted(
        comparisons,
        key=lambda row: (row["final_vs_whisper_cer"], abs(row["delta"] or 0.0)),
        reverse=True,
    )
    for row in disagreements[:60]:
        lines.extend([
            f"### {row['audio']} · 差异 {row['final_vs_whisper_cer']:.3f}",
            "",
            f"- 独立 Whisper：{row['whisper'] or '[空]'}",
            f"- 麦芽实时：{row['preview'] or '[未抽样]'}",
            f"- 麦芽终稿：{row['final'] or '[空]'}",
            f"- 动态词：{', '.join(row['selected_terms']) or '无'}",
            f"- 纠错：{json.dumps(row['corrections'], ensure_ascii=False)}",
            "",
        ])
    report_path = output / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main() -> int:
    args = parse_args()
    result_path = args.output / "results.jsonl"
    if args.engine == "report":
        print(make_report(result_path, args.output, args.references))
        return 0
    recordings = sorted(args.recordings.glob("*.wav"))
    if args.limit is not None:
        recordings = recordings[: max(0, args.limit)]
    rows = latest_rows(read_rows(result_path))
    if args.no_resume:
        completed: set[tuple[str, str]] = set()
    else:
        completed = set(rows)
    if args.engine == "whisper":
        run_whisper(
            recordings,
            model=args.whisper_model,
            result_path=result_path,
            completed=completed,
        )
    else:
        if args.no_resume:
            rows = {
                key: value for key, value in rows.items()
                if key[1] != args.engine
            }
        run_paraformer(
            recordings,
            engine=args.engine,
            model=args.preview_model if args.engine == "preview" else args.final_model,
            result_path=result_path,
            existing=rows,
            glossary_path=args.glossary,
            final_with_preview=args.final_with_preview,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
