#!/usr/bin/env python3
"""Local learning events and train-ready ASR fine-tuning samples."""

from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any
import wave


MAX_TEXT_LENGTH = 8_000
MAX_AUDIO_BYTES = 100 * 1024 * 1024
MAX_AUDIO_SECONDS = 10 * 60
MIN_SHORT_LABEL_SIMILARITY = 0.30
MIN_LONG_LABEL_SIMILARITY = 0.65


class TrainingSampleRejected(ValueError):
    """The feedback is useful as a text rule but unsafe as an audio label."""


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
        try:
            with wave.open(str(source), "rb") as audio:
                channels = audio.getnchannels()
                sample_width = audio.getsampwidth()
                sample_rate = audio.getframerate()
                frames = audio.getnframes()
        except (OSError, EOFError, wave.Error) as error:
            raise TrainingSampleRejected("WAV 录音无法解析") from error
        duration = frames / sample_rate if sample_rate else 0.0
        if channels != 1 or sample_width != 2 or sample_rate != 16_000:
            raise TrainingSampleRejected("录音不是 16 kHz 单声道 PCM16")
        if duration < 0.5 or duration > MAX_AUDIO_SECONDS:
            raise TrainingSampleRejected("录音时长不适合作为微调样本")
        return source, {
            "duration": round(duration, 4),
            "sample_rate": sample_rate,
            "channels": channels,
        }

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
        review_reason: str | None = None
        try:
            reference = extract_corrected_utterance(expected_text, edited_text, final_text)
        except TrainingSampleRejected as error:
            reference = ""
            review_reason = str(error)
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
            "label_status": "user_confirmed" if reference else "needs_review",
            "training_ready": bool(reference),
            "review_reason": review_reason,
            "source": "learn_last_correction",
        }
        if sample_id not in self._known_ids():
            with self.manifest_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        return record
