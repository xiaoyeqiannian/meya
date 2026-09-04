#!/usr/bin/env python3
"""Local learning events and train-ready ASR fine-tuning samples."""

from __future__ import annotations

from array import array
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
from typing import Any
import wave


MAX_TEXT_LENGTH = 8_000
MAX_AUDIO_BYTES = 100 * 1024 * 1024
MAX_AUDIO_SECONDS = 10 * 60
MIN_SHORT_LABEL_SIMILARITY = 0.30
MIN_LONG_LABEL_SIMILARITY = 0.65
QUALITY_FRAME_MS = 20
RECOMMENDED_MIN_RMS_DBFS = -24.0
HARD_MIN_RMS_DBFS = -35.0
MAX_CLIPPING_RATIO = 0.001
MIN_ACTIVE_FRAME_RATIO = 0.20


class TrainingSampleRejected(ValueError):
    """The feedback is useful as a text rule but unsafe as an audio label."""


def _dbfs(value: float) -> float:
    return 20.0 * math.log10(max(value, 1e-9))


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def analyze_audio_quality(
    samples: list[int],
    sample_rate: int = 16_000,
) -> dict[str, Any]:
    """Return deterministic, dependency-free quality metrics for PCM16 audio.

    The quality gate deliberately does not try to repair recordings. Gain can
    be applied to a quiet, clean sample later, but clipped samples cannot be
    recovered and must be reviewed or re-recorded.
    """
    if not samples or sample_rate <= 0:
        return {
            "rms_dbfs": None,
            "peak_dbfs": None,
            "clipping_ratio": 0.0,
            "active_frame_ratio": 0.0,
            "noise_floor_dbfs": None,
            "snr_proxy_db": None,
            "quality_status": "reject",
            "quality_reasons": ["empty_audio"],
        }

    scale = 32768.0
    normalized = [sample / scale for sample in samples]
    rms = math.sqrt(sum(value * value for value in normalized) / len(normalized))
    peak = max(abs(value) for value in normalized)
    clipping_ratio = sum(abs(sample) >= 32760 for sample in samples) / len(samples)
    frame_size = max(1, int(round(sample_rate * QUALITY_FRAME_MS / 1000.0)))
    frame_rms = [
        math.sqrt(
            sum((sample / scale) ** 2 for sample in samples[start:start + frame_size])
            / len(samples[start:start + frame_size])
        )
        for start in range(0, len(samples), frame_size)
        if samples[start:start + frame_size]
    ]
    active_frame_ratio = sum(value >= 0.01 for value in frame_rms) / max(1, len(frame_rms))
    noise_floor = _percentile(frame_rms, 0.10)
    rms_dbfs = _dbfs(rms)
    peak_dbfs = _dbfs(peak)
    noise_floor_dbfs = _dbfs(noise_floor) if noise_floor is not None else None
    snr_proxy_db = (
        rms_dbfs - noise_floor_dbfs
        if noise_floor_dbfs is not None and active_frame_ratio < 0.95
        else None
    )

    reasons: list[str] = []
    if clipping_ratio >= MAX_CLIPPING_RATIO:
        reasons.append("clipping_ratio_over_0.1_percent")
    if rms_dbfs < HARD_MIN_RMS_DBFS:
        reasons.append("rms_below_-35_dbfs")
    elif rms_dbfs < RECOMMENDED_MIN_RMS_DBFS:
        reasons.append("rms_below_recommended_-24_dbfs")
    if active_frame_ratio < MIN_ACTIVE_FRAME_RATIO:
        reasons.append("too_little_active_speech")

    if any(reason in reasons for reason in ("clipping_ratio_over_0.1_percent", "rms_below_-35_dbfs")):
        quality_status = "reject"
    elif reasons:
        quality_status = "needs_review"
    else:
        quality_status = "clean"

    return {
        "rms_dbfs": round(rms_dbfs, 3),
        "peak_dbfs": round(peak_dbfs, 3),
        "clipping_ratio": round(clipping_ratio, 6),
        "active_frame_ratio": round(active_frame_ratio, 4),
        "noise_floor_dbfs": round(noise_floor_dbfs, 3) if noise_floor_dbfs is not None else None,
        "snr_proxy_db": round(snr_proxy_db, 3) if snr_proxy_db is not None else None,
        "quality_status": quality_status,
        "quality_reasons": reasons,
    }


def analyze_wav_quality(path: Path) -> dict[str, Any]:
    """Read a validated WAV and return quality metrics plus format metadata."""
    try:
        with wave.open(str(path), "rb") as audio:
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            sample_rate = audio.getframerate()
            frames = audio.getnframes()
            raw = audio.readframes(frames)
    except (OSError, EOFError, wave.Error) as error:
        raise TrainingSampleRejected("WAV 录音无法解析") from error
    if channels != 1 or sample_width != 2 or sample_rate != 16_000:
        raise TrainingSampleRejected("录音不是 16 kHz 单声道 PCM16")
    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder == "big":
        samples.byteswap()
    quality = analyze_audio_quality(samples, sample_rate)
    return {
        "duration": round(frames / sample_rate if sample_rate else 0.0, 4),
        "sample_rate": sample_rate,
        "channels": channels,
        **quality,
    }


def extract_corrected_utterance(
    expected_text: str,
    edited_text: str,
    final_text: str,
) -> str:
    """Extract only the corrected spoken span from a possibly larger editor value."""
    expected = str(expected_text or "")
    edited = str(edited_text or "")
    hypothesis = str(final_text or "").strip()
    if not expected or not edited or not hypothesis or expected == edited:
        raise TrainingSampleRejected("没有可用的整句修改")

    candidates: list[str] = []
    offset = 0
    while True:
        start = expected.find(hypothesis, offset)
        if start < 0:
            break
        end = start + len(hypothesis)
        prefix = expected[:start]
        suffix = expected[end:]
        if edited.startswith(prefix) and (not suffix or edited.endswith(suffix)):
            corrected_end = len(edited) - len(suffix) if suffix else len(edited)
            corrected = edited[len(prefix):corrected_end].strip()
            if corrected:
                candidates.append(corrected)
        offset = start + 1

    if not candidates:
        raise TrainingSampleRejected("无法将修改文字安全对齐到该段录音")

    corrected = min(
        candidates,
        key=lambda value: abs(len(value) - len(hypothesis)),
    )
    if corrected == hypothesis:
        raise TrainingSampleRejected("语音对应文本没有变化")
    if len(corrected) > MAX_TEXT_LENGTH:
        raise TrainingSampleRejected("修改后文本过长")
    similarity = SequenceMatcher(a=hypothesis, b=corrected, autojunk=False).ratio()
    minimum_similarity = (
        MIN_SHORT_LABEL_SIMILARITY if len(hypothesis) <= 40 else MIN_LONG_LABEL_SIMILARITY
    )
    if similarity < minimum_similarity:
        raise TrainingSampleRejected("修改幅度过大，不适合自动当作音频标签")
    return corrected


def _hash_sample(audio_path: Path, reference: str) -> str:
    digest = hashlib.sha256()
    with audio_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    digest.update(b"\0")
    digest.update(reference.encode("utf-8"))
    return digest.hexdigest()[:24]


class TrainingSampleStore:
    """Persist explicit learning events without adding private content to the rule DB."""

    def __init__(self, user_data_dir: Path, allowed_audio_root: Path):
        self.root = Path(user_data_dir) / "training-data"
        self.audio_dir = self.root / "audio"
        self.manifest_path = self.root / "samples.jsonl"
        self.allowed_audio_root = Path(allowed_audio_root).resolve()

    def _resolve_audio(self, value: str) -> tuple[Path, dict[str, Any]]:
        source = Path(value).expanduser().resolve()
        try:
            source.relative_to(self.allowed_audio_root)
        except ValueError as error:
            raise TrainingSampleRejected("录音不在麦芽录音目录中") from error
        if source.suffix.casefold() != ".wav" or not source.is_file():
            raise TrainingSampleRejected("对应的 WAV 录音不存在")
        size = source.stat().st_size
        if size <= 44 or size > MAX_AUDIO_BYTES:
            raise TrainingSampleRejected("录音文件大小异常")
        audio_metadata = analyze_wav_quality(source)
        duration = float(audio_metadata["duration"])
        if duration < 0.5 or duration > MAX_AUDIO_SECONDS:
            raise TrainingSampleRejected("录音时长不适合作为微调样本")
        return source, audio_metadata

    def _known_ids(self) -> set[str]:
        if not self.manifest_path.exists():
            return set()
        values: set[str] = set()
        for line in self.manifest_path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("sample_id"):
                values.add(str(payload["sample_id"]))
        return values

    def save_feedback(
        self,
        *,
        expected_text: str,
        edited_text: str,
        raw_text: str,
        final_text: str,
        audio_path: str,
        model: str,
    ) -> dict[str, Any]:
        source, audio_metadata = self._resolve_audio(audio_path)
        review_reasons: list[str] = []
        try:
            reference = extract_corrected_utterance(expected_text, edited_text, final_text)
        except TrainingSampleRejected as error:
            reference = ""
            review_reasons.append(str(error))
        if audio_metadata["quality_reasons"]:
            review_reasons.extend(audio_metadata["quality_reasons"])
        identity_text = reference or str(edited_text or "").strip()
        if not identity_text:
            raise TrainingSampleRejected("修改后文本为空")
        sample_id = _hash_sample(source, identity_text)
        relative_audio = Path("audio") / f"{sample_id}.wav"
        destination = self.root / relative_audio

        self.audio_dir.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary = self.audio_dir / f".{sample_id}.tmp"
            try:
                try:
                    os.link(source, temporary)
                except OSError:
                    shutil.copy2(source, temporary)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)

        quality_status = str(audio_metadata["quality_status"])
        label_status = "user_confirmed" if reference else "needs_review"
        if reference and quality_status != "clean":
            label_status = "needs_review"
        record = {
            "schema_version": 1,
            "sample_id": sample_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "audio": relative_audio.as_posix(),
            **audio_metadata,
            "reference": reference,
            "reference_candidate": "" if reference else str(edited_text or "")[:MAX_TEXT_LENGTH],
            "hypothesis": str(final_text or "").strip(),
            "raw_hypothesis": str(raw_text or "").strip(),
            "model": str(model or "").strip(),
            "label_status": label_status,
            "training_ready": bool(reference) and quality_status == "clean",
            "review_reason": "；".join(review_reasons) or None,
            "source": "learn_last_correction",
        }
        if sample_id not in self._known_ids():
            with self.manifest_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        return record
