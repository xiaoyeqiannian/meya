#!/usr/bin/env python3
"""Align ignored Meya recordings with locally stored, sent Codex user turns.

The result is replay-stability evidence, not human transcription truth: a sent
message may itself contain an uncorrected historical ASR error. Private text and
session paths are written only to the caller's ignored runtime output.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from difflib import SequenceMatcher
import glob
import json
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

from history_model_compare import cer, latest_rows, normalize, read_rows


LOCAL_ZONE = ZoneInfo("Asia/Shanghai")
TECHNICAL_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9/_.+-]{1,}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="对齐麦芽录音与 Codex 已发送用户文本")
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--recordings", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, default=Path.home() / ".codex/sessions")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def audio_time(name: str) -> datetime | None:
    try:
        return datetime.strptime(Path(name).stem, "%Y%m%d-%H%M%S").replace(tzinfo=LOCAL_ZONE)
    except ValueError:
        return None


def parse_timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(LOCAL_ZONE)
    except ValueError:
        return None


def user_text(payload: dict[str, Any]) -> str:
    if payload.get("role") != "user":
        return ""
    selected: list[str] = []
    for item in payload.get("content") or []:
        if not isinstance(item, dict) or item.get("type") != "input_text":
            continue
        text = str(item.get("text") or "").strip()
        if not text or text.startswith((
            "<environment_context>",
            "<recommended_plugins>",
            "<heartbeat>",
            "The following is the Codex agent history",
            "Assess the exact planned action",
            "Planned action JSON:",
            ">>>",
        )):
            continue
        selected.append(text)
    value = "\n".join(selected).strip()
    return value if 1 <= len(value) <= 2_000 else ""


def load_messages(sessions: Path, dates: set[str]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for date in sorted(dates):
        year, month, day = date[:4], date[4:6], date[6:8]
        pattern = str(sessions / year / month / day / "*.jsonl")
        for path_value in glob.glob(pattern):
            path = Path(path_value)
            for line in path.open(encoding="utf-8", errors="replace"):
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("type") != "response_item":
                    continue
                text = user_text(item.get("payload") or {})
                created_at = parse_timestamp(item.get("timestamp"))
                if not text or created_at is None:
                    continue
                key = (created_at.isoformat(), text)
                if key in seen:
                    continue
                seen.add(key)
                messages.append({"created_at": created_at, "text": text, "session": path.name})
    return sorted(messages, key=lambda item: item["created_at"])


def similarity(message: str, *hypotheses: str) -> float:
    expected = normalize(message)
    return max(
        (SequenceMatcher(None, expected, normalize(value)).ratio() for value in hypotheses),
        default=0.0,
    )


def spoken_fragment(message: str, *hypotheses: str) -> tuple[str, float]:
    """Select the likely dictated part of a turn that may also contain pasted context."""
    candidates = [message]
    marker = "## My request:"
    if marker in message:
        candidates.append(message.split(marker, 1)[1].strip())
    paragraphs = [value.strip() for value in re.split(r"\n\s*\n", message) if value.strip()]
    candidates.extend(paragraphs)
    candidates.extend("\n\n".join(paragraphs[-count:]) for count in range(1, min(3, len(paragraphs)) + 1))
    lines = [
        line.strip()
        for line in message.splitlines()
        if line.strip()
        and not line.lstrip().startswith(("#", "```", "<image", "[http", "|"))
        and not re.fullmatch(r"[/~].*", line.strip())
    ]
    candidates.extend(lines)
    candidates.extend("\n".join(lines[-count:]) for count in range(1, min(3, len(lines)) + 1))
    unique = list(dict.fromkeys(value for value in candidates if len(normalize(value)) >= 2))
    best = max(unique, key=lambda value: similarity(value, *hypotheses), default=message)
    return best, similarity(best, *hypotheses)


def align(
    recordings: list[Path],
    rows: dict[tuple[str, str], dict[str, Any]],
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for audio in recordings:
        started = audio_time(audio.name)
        whisper = str(rows.get((audio.name, "whisper"), {}).get("final_text") or "")
        final = str(rows.get((audio.name, "final"), {}).get("final_text") or "")
        if started is None or not (whisper or final):
            continue
        candidates: list[tuple[float, float, dict[str, Any]]] = []
        for message in messages:
            delta = (message["created_at"] - started).total_seconds()
            if delta < -5 or delta > 120:
                continue
            fragment, score = spoken_fragment(message["text"], whisper, final)
            rank = score - min(abs(delta), 120.0) / 1_000.0
            candidates.append((
                rank,
                score,
                {**message, "text": fragment, "delta_seconds": delta},
            ))
        if not candidates:
            continue
        _, score, best = max(candidates, key=lambda item: item[0])
        if score < 0.45 and not (best["delta_seconds"] <= 20 and score >= 0.25):
            continue
        reference = str(best["text"])
        output.append({
            "audio": audio.name,
            "audio_time": started.isoformat(),
            "message_time": best["created_at"].isoformat(),
            "delta_seconds": round(float(best["delta_seconds"]), 3),
            "match_similarity": round(score, 4),
            "accepted_text": reference,
            "whisper": whisper,
            "final": final,
            "whisper_cer": cer(reference, whisper),
            "final_cer": cer(reference, final),
            "technical": bool(TECHNICAL_PATTERN.search(reference)),
            "session": best["session"],
        })
    return output


def mean(rows: list[dict[str, Any]], key: str) -> float | None:
    return sum(float(row[key]) for row in rows) / len(rows) if rows else None


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "rows": len(rows),
        "whisper_mean_cer": mean(rows, "whisper_cer"),
        "final_mean_cer": mean(rows, "final_cer"),
        "whisper_exact": sum(normalize(row["whisper"]) == normalize(row["accepted_text"]) for row in rows),
        "final_exact": sum(normalize(row["final"]) == normalize(row["accepted_text"]) for row in rows),
        "whisper_closer": sum(row["whisper_cer"] < row["final_cer"] for row in rows),
        "final_closer": sum(row["final_cer"] < row["whisper_cer"] for row in rows),
        "tie": sum(row["final_cer"] == row["whisper_cer"] for row in rows),
    }


def main() -> int:
    args = parse_args()
    recordings = sorted(args.recordings.glob("*.wav"))
    dates = {path.stem.split("-", 1)[0] for path in recordings}
    messages = load_messages(args.sessions, dates)
    rows = latest_rows(read_rows(args.results))
    aligned = align(recordings, rows, messages)
    high_confidence = [
        row for row in aligned
        if row["match_similarity"] >= 0.75 and row["delta_seconds"] <= 45
    ]
    groups = {
        "all": aligned,
        "high_confidence": high_confidence,
        "high_confidence_technical": [row for row in high_confidence if row["technical"]],
        "high_confidence_plain": [row for row in high_confidence if not row["technical"]],
        "technical": [row for row in aligned if row["technical"]],
        "plain": [row for row in aligned if not row["technical"]],
        "short": [row for row in aligned if len(normalize(row["accepted_text"])) <= 8],
        "long": [row for row in aligned if len(normalize(row["accepted_text"])) > 8],
    }
    summary = {
        "interpretation": "sent Codex text; replay stability, not human audio truth",
        "candidate_messages": len(messages),
        "groups": {name: metrics(values) for name, values in groups.items()},
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "codex-alignment.json").write_text(
        json.dumps({"summary": summary, "rows": aligned}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Codex 已发送文本对齐",
        "",
        "> 这是录音重放稳定性评估，不是人工听写真值；历史已发送文本本身也可能包含未修正的 ASR 错误。",
        "> `high_confidence` 要求文本相似度 ≥ 0.75，且发送时间距录音开始 ≤ 45 秒。",
        "",
        "| 分组 | 对齐数 | Whisper CER | 麦芽终稿 CER | Whisper 完全一致 | 麦芽完全一致 | Whisper 更近 | 麦芽更近 | 持平 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, values in summary["groups"].items():
        lines.append(
            f"| {name} | {values['rows']} | {values['whisper_mean_cer'] or 0:.3f} | "
            f"{values['final_mean_cer'] or 0:.3f} | {values['whisper_exact']} | "
            f"{values['final_exact']} | {values['whisper_closer']} | "
            f"{values['final_closer']} | {values['tie']} |"
        )
    lines.extend(["", "## Whisper 更接近的样本", ""])
    for row in sorted(aligned, key=lambda item: item["final_cer"] - item["whisper_cer"], reverse=True)[:40]:
        lines.extend([
            f"### {row['audio']}", "",
            f"- 已发送：{row['accepted_text']}",
            f"- Whisper：{row['whisper']}",
            f"- 麦芽：{row['final']}", "",
        ])
    (args.output / "codex-alignment.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
