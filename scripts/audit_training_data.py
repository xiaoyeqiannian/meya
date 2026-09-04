#!/usr/bin/env python3
"""Audit and optionally annotate Meya's local speech-training manifest.

Only quality metadata and the training-ready flag are changed. WAV files and
all transcript fields are preserved byte-for-byte.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from training_data import TrainingSampleRejected, analyze_wav_quality  # noqa: E402


def parse_args() -> argparse.Namespace:
    default_root = Path.home() / "Library/Application Support/Meya/training-data"
    parser = argparse.ArgumentParser(description="审计麦芽本地语音训练样本质量")
    parser.add_argument("--root", type=Path, default=default_root)
    parser.add_argument("--apply", action="store_true", help="写回质量字段；默认只读检查")
    return parser.parse_args()


def load_rows(manifest: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def annotate(row: dict[str, Any], manifest_root: Path) -> dict[str, Any]:
    updated = dict(row)
    audio_value = str(row.get("audio") or "")
    audio_path = (manifest_root / audio_value).resolve()
    try:
        audio_path.relative_to(manifest_root.resolve())
        quality = analyze_wav_quality(audio_path)
    except (OSError, TrainingSampleRejected, ValueError):
        quality = {
            "rms_dbfs": None,
            "peak_dbfs": None,
            "clipping_ratio": None,
            "active_frame_ratio": None,
            "noise_floor_dbfs": None,
            "snr_proxy_db": None,
            "quality_status": "reject",
            "quality_reasons": ["audio_missing_or_invalid"],
        }

    for key in (
        "rms_dbfs",
        "peak_dbfs",
        "clipping_ratio",
        "active_frame_ratio",
        "noise_floor_dbfs",
        "snr_proxy_db",
        "quality_status",
        "quality_reasons",
    ):
        updated[key] = quality[key]

    label_reasons = []
    if not row.get("reference") and row.get("review_reason"):
        label_reasons.append(str(row["review_reason"]))
    label_reasons.extend(str(reason) for reason in quality["quality_reasons"])
    updated["review_reason"] = "；".join(dict.fromkeys(label_reasons)) or None
    has_reference = bool(str(row.get("reference") or "").strip())
    updated["label_status"] = (
        "user_confirmed"
        if has_reference and quality["quality_status"] == "clean"
        else "needs_review"
    )
    updated["training_ready"] = has_reference and quality["quality_status"] == "clean"
    return updated


def main() -> int:
    args = parse_args()
    root = args.root.expanduser().resolve()
    manifest = root / "samples.jsonl"
    if not manifest.is_file():
        print(f"未找到样本清单：{manifest}", file=sys.stderr)
        return 2

    rows = load_rows(manifest)
    updated = [annotate(row, root) for row in rows]
    status_counts: dict[str, int] = {}
    ready_count = 0
    for row in updated:
        status = str(row["quality_status"])
        status_counts[status] = status_counts.get(status, 0) + 1
        ready_count += int(bool(row["training_ready"]))
        print(
            f"{row.get('sample_id', '?')}: {status} "
            f"rms={row.get('rms_dbfs')}dBFS "
            f"clip={row.get('clipping_ratio')} "
            f"reasons={','.join(row.get('quality_reasons') or []) or '-'}"
        )
    print(f"样本总数：{len(updated)} · 可训练：{ready_count} · 质量状态：{status_counts}")

    if args.apply:
        backup = manifest.with_name(manifest.name + ".bak")
        shutil.copy2(manifest, backup)
        temporary = manifest.with_name(manifest.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            for row in updated:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.replace(temporary, manifest)
        print(f"已写回质量元数据，原清单备份：{backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
