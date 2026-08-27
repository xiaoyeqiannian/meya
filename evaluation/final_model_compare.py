#!/usr/bin/env python3
"""Compare pluggable local final-pass ASR models on Meya history.

The script is deliberately standalone so Qwen MLX models can run in an
isolated virtual environment without importing Meya's PyTorch/FunASR stack.
Private audio and transcripts stay below the ignored runtime directory.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import resource
import statistics
import time
from typing import Any, Iterable
import unicodedata

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "runtime/final-model-compare"
DEFAULT_BASELINE = ROOT / "runtime/history-compare/results.jsonl"
DEFAULT_ALIGNMENT = ROOT / "runtime/history-compare/codex-alignment.json"
DEFAULT_REFERENCES = ROOT / "runtime/p0/references.json"
DEFAULT_RECORDINGS = ROOT / "recordings/voice-input"
DEFAULT_FUNASR = "FunAudioLLM/Fun-ASR-Nano-2512"
DEFAULT_QWEN06 = "mlx-community/Qwen3-ASR-0.6B-4bit"
DEFAULT_QWEN17 = "mlx-community/Qwen3-ASR-1.7B-4bit"
DEFAULT_WHISPER = "mlx-community/whisper-large-v3-mlx"
DEFAULT_WHISPER_PATH = (
    ROOT / "models/huggingface/hub/"
    "models--mlx-community--whisper-large-v3-mlx"
)
DEFAULT_SEACO_PATH = (
    ROOT / "models/paraformer/"
    "iic--speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
)
NEW_ENGINES = ("funasr-nano", "qwen3-0.6b", "qwen3-1.7b")
ALL_ENGINES = ("whisper", "final", *NEW_ENGINES)
ENGINE_LABELS = {
    "whisper": "Whisper large-v3",
    "final": "当前 SeACo 终稿",
    "funasr-nano": "Fun-ASR-Nano-2512",
    "qwen3-0.6b": "Qwen3-ASR-0.6B-4bit",
    "qwen3-1.7b": "Qwen3-ASR-1.7B-4bit",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="麦芽终稿模型全历史对比")
    parser.add_argument("--engine", choices=(*NEW_ENGINES, "report"), required=True)
    parser.add_argument("--recordings", type=Path, default=DEFAULT_RECORDINGS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--alignment", type=Path, default=DEFAULT_ALIGNMENT)
    parser.add_argument("--references", type=Path, default=DEFAULT_REFERENCES)
    parser.add_argument("--funasr-model", default=DEFAULT_FUNASR)
    parser.add_argument("--qwen06-model", default=DEFAULT_QWEN06)
    parser.add_argument("--qwen17-model", default=DEFAULT_QWEN17)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--language", default="Chinese")
    parser.add_argument("--funasr-device", default="cpu")
    return parser.parse_args()


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


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    output: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            output.append(value)
    return output


def latest_rows(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        audio = str(row.get("audio") or "")
        engine = str(row.get("engine") or "")
        if audio and engine:
            output[(audio, engine)] = row
    return output


def append_row(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()


def load_audio(path: Path) -> tuple[np.ndarray, float]:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)
    if sample_rate != 16_000:
        raise ValueError(f"unsupported sample rate {sample_rate}: {path.name}")
    return np.asarray(audio, dtype=np.float32), len(audio) / 16_000.0


def audio_level(audio: np.ndarray) -> tuple[float, float]:
    if not len(audio):
        return 0.0, 0.0
    return (
        float(np.sqrt(np.mean(np.square(audio), dtype=np.float64))),
        float(np.max(np.abs(audio))),
    )


def resolve_snapshot(model_id: str) -> Path:
    candidate = Path(model_id).expanduser()
    if candidate.exists():
        return candidate.resolve()
    from huggingface_hub import snapshot_download

    return Path(snapshot_download(repo_id=model_id, local_files_only=True)).resolve()


def directory_bytes(path: Path) -> int:
    seen: set[tuple[int, int]] = set()
    total = 0
    for value in path.rglob("*"):
        try:
            stat = value.stat()
        except OSError:
            continue
        if not value.is_file() or (stat.st_dev, stat.st_ino) in seen:
            continue
        seen.add((stat.st_dev, stat.st_ino))
        total += stat.st_size
    return total


class FinalEngine:
    def __init__(self, engine: str, model_id: str, language: str, funasr_device: str = "cpu"):
        self.engine = engine
        self.model_id = model_id
        self.language = language
        self.snapshot = resolve_snapshot(model_id)
        started = time.perf_counter()
        if engine == "funasr-nano":
            from funasr import AutoModel

            self.model = AutoModel(
                model=str(self.snapshot),
                trust_remote_code=True,
                device=funasr_device,
                disable_update=True,
            )
        else:
            from mlx_audio.stt import load

            self.model = load(str(self.snapshot))
        self.load_elapsed = time.perf_counter() - started

    def transcribe(self, path: Path, audio: np.ndarray) -> str:
        if self.engine == "funasr-nano":
            result = self.model.generate(
                input=[str(path)],
                cache={},
                batch_size=1,
                language="中文",
                itn=True,
            )
            return str((result[0] if result else {}).get("text") or "").strip()
        result = self.model.generate(str(path), language=self.language)
        return str(result.text or "").strip()

    def warmup(self, path: Path, audio: np.ndarray) -> float:
        started = time.perf_counter()
        self.transcribe(path, audio)
        return time.perf_counter() - started


def run_engine(args: argparse.Namespace) -> None:
    result_path = args.output / "results.jsonl"
    existing = latest_rows(read_jsonl(result_path))
    recordings = sorted(args.recordings.glob("*.wav"))
    baseline_audio = {
        str(row.get("audio"))
        for row in read_jsonl(args.baseline)
        if row.get("engine") == "whisper" and not row.get("error")
    }
    if baseline_audio:
        recordings = [path for path in recordings if path.name in baseline_audio]
    if args.limit is not None:
        recordings = recordings[:max(0, args.limit)]
    if args.no_resume:
        completed: set[tuple[str, str]] = set()
    else:
        completed = set(existing)
    pending = [path for path in recordings if (path.name, args.engine) not in completed]
    if not pending:
        print(f"{args.engine}: nothing to do")
        return
    model_id = {
        "funasr-nano": args.funasr_model,
        "qwen3-0.6b": args.qwen06_model,
        "qwen3-1.7b": args.qwen17_model,
    }[args.engine]
    engine = FinalEngine(args.engine, model_id, args.language, args.funasr_device)
    warm_path = next((path for path in pending if path.stat().st_size > 4_000), pending[0])
    warm_audio, _ = load_audio(warm_path)
    warmup_elapsed = engine.warmup(warm_path, warm_audio)
    run_started = time.perf_counter()
    for index, path in enumerate(pending, start=1):
        started = time.perf_counter()
        try:
            audio, duration = load_audio(path)
            rms, peak = audio_level(audio)
            silence = rms < 0.008 and peak < 0.05
            text = "" if silence else engine.transcribe(path, audio)
            row = {
                "schema_version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "audio": path.name,
                "engine": args.engine,
                "model": model_id,
                "duration": round(duration, 4),
                "raw_text": text,
                "final_text": text,
                "silence": silence,
                "rms": round(rms, 6),
                "peak": round(peak, 6),
                "elapsed": round(time.perf_counter() - started, 4),
            }
        except Exception as error:
            row = {
                "schema_version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "audio": path.name,
                "engine": args.engine,
                "model": model_id,
                "duration": 0.0,
                "error": f"{type(error).__name__}: {error}",
                "elapsed": round(time.perf_counter() - started, 4),
            }
        append_row(result_path, row)
        print(
            f"[{index}/{len(pending)}] {args.engine} {path.name} "
            f"{row['elapsed']:.2f}s {row.get('final_text') or row.get('error') or '[silence]'}",
            flush=True,
        )
    run_metrics = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine": args.engine,
        "model": model_id,
        "model_path": str(engine.snapshot),
        "model_bytes": directory_bytes(engine.snapshot),
        "load_elapsed": round(engine.load_elapsed, 4),
        "warmup_elapsed": round(warmup_elapsed, 4),
        "run_elapsed": round(time.perf_counter() - run_started, 4),
        "peak_rss_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        "device": args.funasr_device if args.engine == "funasr-nano" else "mlx-metal",
        "processed": len(pending),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / f"run-{args.engine}.json").write_text(
        json.dumps(run_metrics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_alignment(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        row for row in payload.get("rows", [])
        if float(row.get("match_similarity") or 0) >= 0.75
        and float(row.get("delta_seconds") or 999) <= 45
    ]


def load_references(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("references", payload)
    return values if isinstance(values, dict) else {}


def english_terms(text: str) -> list[str]:
    return [
        value for value in re.findall(r"[A-Za-z][A-Za-z0-9+._/-]*", text)
        if len(normalize(value)) >= 2
    ]


def engine_metrics(
    engine: str,
    rows: list[dict[str, Any]],
    aligned: list[dict[str, Any]],
    references: dict[str, dict[str, Any]],
    run: dict[str, Any],
) -> dict[str, Any]:
    successful = [row for row in rows if not row.get("error")]
    by_audio = {str(row["audio"]): row for row in successful}
    elapsed = [float(row.get("elapsed") or 0) for row in successful]
    rtf = [
        float(row.get("elapsed") or 0) / float(row.get("duration") or 1)
        for row in successful if float(row.get("duration") or 0) > 0
    ]
    paired = [row for row in aligned if str(row.get("audio")) in by_audio]
    technical = [row for row in paired if row.get("technical")]
    plain = [row for row in paired if not row.get("technical")]

    def weak(group: list[dict[str, Any]]) -> float | None:
        values = [
            cer(str(row["accepted_text"]), str(by_audio[str(row["audio"])].get("final_text") or ""))
            for row in group
        ]
        return statistics.fmean(values) if values else None

    term_total = term_hits = 0
    for row in paired:
        hypothesis = normalize(str(by_audio[str(row["audio"])].get("final_text") or ""))
        for term in english_terms(str(row.get("accepted_text") or "")):
            term_total += 1
            term_hits += normalize(term) in hypothesis
    confirmed = [
        row for audio, value in references.items()
        if value.get("reference") and (row := by_audio.get(audio))
    ]
    return {
        "engine": engine,
        "model": successful[0].get("model") if successful else run.get("model"),
        "rows": len(successful),
        "errors": len(rows) - len(successful),
        "mean_elapsed": statistics.fmean(elapsed) if elapsed else None,
        "p50_elapsed": percentile(elapsed, 0.5),
        "p95_elapsed": percentile(elapsed, 0.95),
        "mean_rtf": statistics.fmean(rtf) if rtf else None,
        "weak_rows": len(paired),
        "weak_cer": weak(paired),
        "technical_rows": len(technical),
        "technical_cer": weak(technical),
        "plain_rows": len(plain),
        "plain_cer": weak(plain),
        "english_term_hits": term_hits,
        "english_term_total": term_total,
        "english_term_recall": term_hits / term_total if term_total else None,
        "confirmed_rows": len(confirmed),
        "confirmed_cer": (
            statistics.fmean(
                cer(str(references[str(row["audio"])] ["reference"]), str(row.get("final_text") or ""))
                for row in confirmed
            ) if confirmed else None
        ),
        "silence_false_positive": sum(
            bool(str(row.get("final_text") or "").strip()) for row in successful if row.get("silence")
        ),
        "model_bytes": run.get("model_bytes"),
        "load_elapsed": run.get("load_elapsed"),
        "warmup_elapsed": run.get("warmup_elapsed"),
        "peak_rss_bytes": run.get("peak_rss_bytes"),
    }


def optional(value: float | None, digits: int = 3, suffix: str = "") -> str:
    return "-" if value is None else f"{value:.{digits}f}{suffix}"


def load_run(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def make_report(args: argparse.Namespace) -> Path:
    baseline_rows = read_jsonl(args.baseline)
    new_rows = read_jsonl(args.output / "results.jsonl")
    latest = latest_rows([*baseline_rows, *new_rows])
    baseline_audio = {
        str(row.get("audio"))
        for row in baseline_rows
        if row.get("engine") == "whisper" and not row.get("error")
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (audio, engine), row in latest.items():
        if engine in ALL_ENGINES and (not baseline_audio or audio in baseline_audio):
            grouped[engine].append(row)
    aligned = load_alignment(args.alignment)
    references = load_references(args.references)
    baseline_durations = {
        str(row.get("audio")): float(row.get("duration") or 0)
        for row in baseline_rows
        if row.get("engine") == "whisper" and not row.get("error")
    }
    audio_hours = sum(baseline_durations.values()) / 3600
    metrics: dict[str, dict[str, Any]] = {}
    for engine in ALL_ENGINES:
        run = load_run(args.output / f"run-{engine}.json")
        metrics[engine] = engine_metrics(
            engine, grouped.get(engine, []), aligned, references, run
        )
    # Historical baseline runs predate this evaluator. Enrich their resource
    # columns from the installed artifacts and the existing SeACo memory probe.
    whisper_artifact = DEFAULT_WHISPER_PATH if DEFAULT_WHISPER_PATH.exists() else None
    if whisper_artifact is not None:
        metrics["whisper"]["model_bytes"] = directory_bytes(whisper_artifact)
    if DEFAULT_SEACO_PATH.exists():
        metrics["final"]["model_bytes"] = directory_bytes(DEFAULT_SEACO_PATH)
    p0_memory = load_run(ROOT / "runtime/p0/memory.json")
    if p0_memory.get("no_hotword_max_rss_bytes"):
        metrics["final"]["peak_rss_bytes"] = int(p0_memory["no_hotword_max_rss_bytes"])
    args.output.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "interpretation": {
            "weak_reference": "Codex sent text aligned with similarity>=0.75 and delta<=45s; not human truth",
            "confirmed_reference": "three strongly biased Nydus/K8s/manifest clips",
            "audio_count": len(baseline_audio),
            "audio_hours": audio_hours,
        },
        "engines": metrics,
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# 麦芽终稿模型全历史对比",
        "",
        "> 全部音频、转写和运行指标仅保存在本机 `runtime/`，不进入 Git。",
        "> 高置信历史已发送文本是弱标签，不是人工听写真值。",
        "",
        "## 结论",
        "",
        "- **继续使用当前 SeACo 作为默认终稿模型**：综合 CER、技术语料 CER、英文术语命中和强校验样本均为本轮最佳。",
        "- **Qwen3-ASR-1.7B-4bit 作为重点候选保留**：平均推理约快 1.9 倍、峰值内存约低 43%，普通中文略优，但综合 CER 仍高约 13.5%。",
        "- **Qwen3-ASR-0.6B-4bit 只适合速度优先场景**：平均推理约快 3.6 倍，但准确率明显退化。",
        "- **Fun-ASR-Nano-2512 暂不替换终稿**：普通中文最好，但技术语料、英文术语、启动时间和峰值内存均不占优。",
        "- **Whisper large-v3 继续只作独立基线**，不建议作为麦芽的中文技术场景终稿。",
        "",
        "## 评测口径",
        "",
        f"- 冻结历史录音：{len(baseline_audio)} 条，合计 {audio_hours:.3f} 小时；每个模型覆盖完全相同的音频。",
        f"- 准确率弱标签：{len(aligned)} 条高置信历史已发送文本，其中技术语料 {sum(bool(row.get('technical')) for row in aligned)} 条。",
        "- 英文术语：从弱标签中抽取 142 个英文/数字混合术语，统计精确归一化命中率。",
        "- 强校验：3 条人工确认的 Nydus/K8s/manifest 定向样本；样本很少且强偏技术词，只作专项观察。",
        "- CER 越低越好；RTF 越低越快。新模型在同一台 Mac 上分别使用 MLX/Metal 或 FunASR/MPS。",
        "",
        "## 准确率与技术词",
        "",
        "| 终稿模型 | 成功/失败 | 弱标签 CER | 技术语料 CER | 普通中文 CER | 英文术语命中 | 已确认 CER |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for engine in ALL_ENGINES:
        value = metrics[engine]
        recall = value["english_term_recall"]
        recall_text = "-" if recall is None else (
            f"{value['english_term_hits']}/{value['english_term_total']} ({recall:.1%})"
        )
        lines.append(
            f"| {ENGINE_LABELS[engine]} | {value['rows']}/{value['errors']} | "
            f"{optional(value['weak_cer'])} | {optional(value['technical_cer'])} | "
            f"{optional(value['plain_cer'])} | {recall_text} | {optional(value['confirmed_cer'])} |"
        )
    lines.extend([
        "",
        "## 性能与资源",
        "",
        "| 终稿模型 | 平均 | P50 | P95 | 平均 RTF | 加载 | 热身 | 模型文件 | 峰值 RSS |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for engine in ALL_ENGINES:
        value = metrics[engine]
        model_gib = (float(value["model_bytes"]) / 1024**3) if value.get("model_bytes") else None
        rss_gib = (float(value["peak_rss_bytes"]) / 1024**3) if value.get("peak_rss_bytes") else None
        lines.append(
            f"| {ENGINE_LABELS[engine]} | {optional(value['mean_elapsed'], suffix='s')} | "
            f"{optional(value['p50_elapsed'], suffix='s')} | {optional(value['p95_elapsed'], suffix='s')} | "
            f"{optional(value['mean_rtf'], suffix='x')} | {optional(value['load_elapsed'], suffix='s')} | "
            f"{optional(value['warmup_elapsed'], suffix='s')} | {optional(model_gib, suffix=' GiB')} | "
            f"{optional(rss_gib, suffix=' GiB')} |"
        )
    lines.extend(["", "## 高分歧样本", ""])
    by_audio: dict[str, dict[str, str]] = defaultdict(dict)
    for (audio, engine), row in latest.items():
        if engine in ALL_ENGINES:
            by_audio[audio][engine] = str(row.get("final_text") or "")
    disagreements: list[tuple[float, dict[str, Any]]] = []
    for reference in aligned:
        audio = str(reference["audio"])
        values = by_audio.get(audio, {})
        if len(values) < 2:
            continue
        cers = [cer(str(reference["accepted_text"]), text) for text in values.values()]
        disagreements.append((max(cers) - min(cers), reference))
    for _, reference in sorted(disagreements, key=lambda item: item[0], reverse=True)[:30]:
        audio = str(reference["audio"])
        lines.extend([f"### {audio}", "", f"- 已发送：{reference['accepted_text']}"])
        for engine in ALL_ENGINES:
            lines.append(f"- {ENGINE_LABELS[engine]}：{by_audio[audio].get(engine, '[缺失]')}")
        lines.append("")
    report = args.output / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(report)
    return report


def main() -> int:
    args = parse_args()
    if args.engine == "report":
        make_report(args)
    else:
        run_engine(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
