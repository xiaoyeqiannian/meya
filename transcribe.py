#!/usr/bin/env python3
"""在 Apple Silicon 上使用 MLX Whisper 进行完全本地的语音识别。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
import wave

import numpy as np
from scipy.signal import resample_poly

from glossary import apply_glossary_corrections, glossary_hotwords, load_glossary


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"


def user_data_dir() -> Path:
    if override := os.environ.get("MEYA_USER_DATA"):
        return Path(override)
    return Path.home() / "Library" / "Application Support" / "Meya"


def user_file(name: str, fallback_in_project: bool = True) -> Path:
    candidate = user_data_dir() / name
    if candidate.exists() or not fallback_in_project:
        return candidate
    return PROJECT_DIR / name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="本地 MLX Whisper 转写")
    parser.add_argument("audio", type=Path, help="16-bit PCM WAV 音频")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="MLX/Hugging Face 模型名或本地路径")
    parser.add_argument("--terms", type=Path, default=user_file("terms.txt"), help="术语表")
    parser.add_argument(
        "--glossary",
        type=Path,
        default=user_file("glossary.tsv", fallback_in_project=False),
        help="结构化术语词典（TAB 分隔）",
    )
    parser.add_argument(
        "--corrections",
        type=Path,
        default=user_file("corrections.tsv"),
        help="本地文本纠错表（TAB 分隔）",
    )
    parser.add_argument(
        "--language",
        default="auto",
        help="语言代码；默认 auto，由模型按听到的内容判断，不强制中文",
    )
    parser.add_argument("--no-prompt", action="store_true", help="不将术语表作为上下文提示")
    parser.add_argument("--no-corrections", action="store_true", help="不应用本地文本纠错表")
    parser.add_argument("--offline", action="store_true", help="禁止联网，仅使用已下载模型")
    parser.add_argument("--min-rms", type=float, default=0.008, help="低于该音量时视为静音；设为 0 可禁用")
    parser.add_argument("--json", action="store_true", help="额外输出分段 JSON")
    return parser.parse_args()


def load_wav(path: Path, target_rate: int = 16000) -> tuple[np.ndarray, float]:
    if not path.exists():
        raise FileNotFoundError(f"找不到音频文件: {path}")

    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())

    if sample_width != 2:
        raise ValueError(f"仅支持 16-bit PCM WAV，当前为 {sample_width * 8}-bit")

    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if sample_rate != target_rate:
        from math import gcd

        common = gcd(sample_rate, target_rate)
        audio = resample_poly(audio, target_rate // common, sample_rate // common).astype(np.float32)

    duration = len(audio) / target_rate
    return np.ascontiguousarray(audio), duration


def resolve_whisper_language(language: str | None) -> str | None:
    """Return a Whisper language code, or None to let the model detect it."""
    if language is None:
        return None
    value = language.strip().lower()
    if value in {"", "auto", "detect", "none"}:
        return None
    return value


def resolve_decode_language(requested: str | None, cached: str | None = None) -> str | None:
    """Only an explicit request pins the language. Never reuse a previous detect."""
    return resolve_whisper_language(requested)


def is_untrusted_preview_text(
    text: str,
    duration: float,
    expected_language: str | None = None,
) -> bool:
    """Drop short Whisper hallucinations before they can become the live draft."""
    cleaned = " ".join((text or "").strip().strip(".").split()).lower()
    if not cleaned:
        return True
    boilerplate = {
        "thank you",
        "thanks",
        "thanks for watching",
        "thank you for watching",
        "thank you for listening",
        "thanks for listening",
        "please subscribe",
        "you",
        "bye",
        "the",
        "i",
    }
    if cleaned in boilerplate and duration < 2.0:
        return True
    if duration < 0.8 and cleaned.isascii() and len(cleaned) <= 16:
        return True
    if (expected_language or "").lower() == "zh":
        has_cjk = any("\u3400" <= char <= "\u9fff" for char in cleaned)
        if duration < 2.2 and cleaned.isascii() and not has_cjk:
            return True
        foreign_ranges = (
            ("\u3040", "\u30ff"),  # Japanese kana
            ("\u0400", "\u052f"),  # Cyrillic
            ("\u0600", "\u06ff"),  # Arabic
            ("\u0900", "\u097f"),  # Devanagari
            ("\u0e00", "\u0e7f"),  # Thai
            ("\uac00", "\ud7af"),  # Hangul
        )
        has_foreign_script = any(
            start <= char <= end
            for char in cleaned
            for start, end in foreign_ranges
        )
        if duration < 3.0 and has_foreign_script and not has_cjk:
            return True
    return False


def is_speakable_term(term: str) -> bool:
    """Keep prompt entries short, pronounceable, and useful in dictated speech."""
    value = term.strip()
    compact = "".join(value.split())
    if not 2 <= len(compact) <= 16 or len(value.split()) > 4:
        return False
    lowered = value.lower()
    if "://" in lowered or lowered.startswith("www.") or value.startswith("/"):
        return False
    if "\\" in value or "_" in value:
        return False
    if re.search(r"[{}\[\]<>＝=。！？!?，,；;：:]", value):
        return False
    if len(compact) <= 2 and compact.isascii() and compact.isalnum():
        return False
    return True


def load_terms(path: Path) -> list[str]:
    if not path.exists():
        return []
    terms: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        key = value.casefold()
        if not value or value.startswith("#") or not is_speakable_term(value) or key in seen:
            continue
        seen.add(key)
        terms.append(value)
        if len(terms) >= 100:
            break
    return terms


def load_personal_hotwords(terms_path: Path, glossary_path: Path) -> list[str]:
    entries = load_glossary(glossary_path)
    return glossary_hotwords(entries) if entries else load_terms(terms_path)


def apply_corrections(text: str, path: Path) -> tuple[str, list[tuple[str, str]]]:
    if not path.exists():
        return text, []
    changed: list[tuple[str, str]] = []
    corrected = text
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 2:
            print(f"警告: {path}:{line_number} 不是两列 TAB 格式", file=sys.stderr)
            continue
        source, target = parts
        if source and source in corrected:
            corrected = corrected.replace(source, target)
            changed.append((source, target))
    return corrected, changed


def main() -> int:
    args = parse_args()

    # 将模型放在项目目录，便于确认离线运行和整体删除。
    model_home = PROJECT_DIR / "models" / "huggingface"
    model_home.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(model_home))
    os.environ.setdefault("HF_HUB_CACHE", str(model_home / "hub"))
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"

    try:
        audio, duration = load_wav(args.audio)
    except (FileNotFoundError, ValueError, wave.Error) as exc:
        print(f"音频错误: {exc}", file=sys.stderr)
        return 2

    terms = [] if args.no_prompt else load_personal_hotwords(args.terms, args.glossary)
    prompt = None
    if terms:
        prompt = "以下是可能出现的专有名词，请保持原有写法：" + "、".join(terms) + "。"

    print(f"音频: {args.audio} ({duration:.1f} 秒)")
    rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64))) if len(audio) else 0.0
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    print(f"音量: RMS {rms:.4f}, peak {peak:.4f}")
    print(f"模型: {args.model}")
    print("模式: " + ("严格离线" if args.offline else "本地推理（缺少模型时会下载）"))
    if terms:
        print(f"术语提示: {len(terms)} 个")

    # Whisper 在纯静音上加入专有名词提示时可能重复“幻听”这些词。
    # 在调用模型前做一道保守的音量门检查。
    if args.min_rms > 0 and rms < args.min_rms and peak < 0.05:
        print("\n=== 识别结果 ===")
        print("(未检测到明显语音)")
        return 0

    started = time.perf_counter()
    try:
        import mlx_whisper

        transcribe_options = {
            "path_or_hf_repo": args.model,
            "task": "transcribe",
            "initial_prompt": prompt,
            "temperature": 0.0,
            "condition_on_previous_text": True,
            "verbose": False,
        }
        if language := resolve_whisper_language(args.language):
            transcribe_options["language"] = language
        result = mlx_whisper.transcribe(audio, **transcribe_options)
    except Exception as exc:
        print(f"转写失败: {exc}", file=sys.stderr)
        if args.offline:
            print("提示：先去掉 --offline 运行一次以下载模型。", file=sys.stderr)
        return 1

    elapsed = time.perf_counter() - started
    raw_text = result.get("text", "").strip()
    if args.no_corrections:
        text, corrections = raw_text, []
    else:
        text, glossary_changes = apply_glossary_corrections(
            raw_text,
            load_glossary(args.glossary),
        )
        text, legacy_changes = apply_corrections(text, args.corrections)
        corrections = glossary_changes + legacy_changes
    print("\n=== 识别结果 ===")
    if corrections:
        print("模型原文: " + raw_text)
        print("本地纠错: " + "；".join(f"{source} → {target}" for source, target in corrections))
        print("最终文本: ", end="")
    print(text or "(未识别到文本)")
    print(f"\n用时: {elapsed:.2f} 秒，实时率: {elapsed / max(duration, 0.001):.2f}x")

    if args.json:
        print("\n=== JSON ===")
        print(json.dumps(result.get("segments", []), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
