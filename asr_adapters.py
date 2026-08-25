"""Pluggable local ASR backends used by the persistent Meya worker."""

from __future__ import annotations

import os
from pathlib import Path
import re
import tempfile
from typing import Any

import numpy as np

from glossary import GlossaryEntry, glossary_hotwords
from seaco_hotwords import compile_glossary, write_compilation_report


PARAFORMER_PREFIX = "paraformer:"
DEFAULT_PARAFORMER_MODEL = "funasr/paraformer-zh"
DEFAULT_PUNCTUATION_MODEL = "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch"
STREAMING_CHUNK_SIZE = [0, 10, 5]
STREAMING_CHUNK_SAMPLES = STREAMING_CHUNK_SIZE[1] * 960


def write_hotword_file(path: Path, hotwords: list[str]) -> Path:
    """Write one complete hotword per line for FunASR/SeACo.

    Passing a whitespace-joined string makes FunASR split multi-word terms such
    as ``Acme CLI`` into unrelated hotwords. A .txt file preserves each line as
    one phrase while keeping inference completely local.
    """
    selected: list[str] = []
    seen: set[str] = set()
    for raw in hotwords[:100]:
        value = " ".join(raw.strip().split())
        key = value.casefold()
        if value and key not in seen:
            selected.append(value)
            seen.add(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(selected) + ("\n" if selected else "")
    if path.exists() and path.read_text(encoding="utf-8") == body:
        return path
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(body)
        temporary = Path(handle.name)
    os.replace(temporary, path)
    return path


def split_model_identifier(identifier: str) -> tuple[str, str]:
    """Return ``(backend, model)`` while keeping old Whisper configs valid."""
    value = identifier.strip()
    if value.lower().startswith(PARAFORMER_PREFIX):
        model = value[len(PARAFORMER_PREFIX) :].strip()
        if not model:
            raise ValueError("Paraformer 模型标识不能为空")
        return "paraformer", model
    return "whisper", value


def paraformer_identifier(model: str) -> str:
    return PARAFORMER_PREFIX + model


def resolve_paraformer_source(project_dir: Path, model: str) -> Path:
    """Resolve an explicitly local Paraformer model; recognition stays offline."""
    source = Path(model).expanduser()
    if not source.is_absolute():
        bundled = project_dir / "models" / "paraformer" / model.replace("/", "--")
        if bundled.exists():
            source = bundled
    if not source.exists():
        raise FileNotFoundError(
            f"Paraformer 模型尚未下载：{model}。"
            "请在麦芽项目目录运行 ./download_paraformer.sh"
        )
    missing = [name for name in ("config.yaml", "model.pt") if not (source / name).exists()]
    if missing:
        raise FileNotFoundError(
            f"Paraformer 模型目录不完整，缺少 {', '.join(missing)}：{source}"
        )
    return source.resolve()


def resolve_punctuation_source(project_dir: Path) -> Path | None:
    source = (
        project_dir
        / "models"
        / "punctuation"
        / DEFAULT_PUNCTUATION_MODEL.replace("/", "--")
    )
    required = ("config.yaml", "model.pt")
    return source.resolve() if all((source / name).exists() for name in required) else None


class ParaformerAdapter:
    """FunASR Paraformer adapter with the same result shape as MLX Whisper."""

    backend = "paraformer"

    def __init__(self, project_dir: Path, model: str, role: str = "preview") -> None:
        self.identifier = paraformer_identifier(model)
        self.source = resolve_paraformer_source(project_dir, model)
        self.role = role
        self.hotword_file = project_dir / "runtime" / f"seaco-hotwords-{role}.txt"
        config = (self.source / "config.yaml").read_text(encoding="utf-8")
        self.is_streaming = bool(
            re.search(r"^model:\s*(?:ParaformerStreaming|EParaformer)\s*$", config, re.MULTILINE)
        )
        self.is_seaco = bool(re.search(r"^model:\s*SeacoParaformer\s*$", config, re.MULTILINE))
        self.seg_dict_path = self.source / "seg_dict"
        self.report_path = project_dir / "runtime" / "hotword-report.json"
        self.punctuation_source = (
            resolve_punctuation_source(project_dir) if role == "final" and not self.is_streaming else None
        )
        self.model: Any | None = None
        self._stream_cache: dict[str, Any] = {}
        self._stream_text = ""
        self._processed_until = 0.0

    def prepare_hotwords(
        self,
        entries: list[GlossaryEntry],
        *,
        max_terms: int = 100,
        max_chars: int = 1_000,
        max_forms_per_entry: int | None = None,
    ) -> list[str]:
        if not self.is_seaco or not self.seg_dict_path.exists():
            return glossary_hotwords(entries, max_terms=max_terms, max_chars=max_chars)
        compilation = compile_glossary(
            entries,
            self.seg_dict_path,
            max_terms=max_terms,
            max_chars=max_chars,
            max_forms_per_entry=max_forms_per_entry,
        )
        write_compilation_report(
            self.report_path,
            compilation,
            model=self.identifier,
        )
        selected = list(compilation.selected_hotwords)
        write_hotword_file(self.hotword_file, selected)
        return selected

    def load(self) -> None:
        # FunASR itself is model-agnostic, while this adapter pins it to a local
        # directory so opening Meya never contacts ModelScope or Hugging Face.
        from funasr import AutoModel

        options: dict[str, Any] = dict(
            model=str(self.source),
            device="cpu",
            ncpu=max(2, min(6, os.cpu_count() or 4)),
            disable_update=True,
            disable_pbar=True,
            log_level="ERROR",
        )
        if self.punctuation_source is not None:
            options["punc_model"] = str(self.punctuation_source)
        self.model = AutoModel(**options)

    def warmup(self) -> None:
        if self.model is None:
            raise RuntimeError("Paraformer 模型尚未加载")
        audio = np.zeros(3_200, dtype=np.float32)
        audio[:400] = 0.02
        if self.is_streaming:
            cache: dict[str, Any] = {}
            self.model.generate(
                input=audio,
                cache=cache,
                is_final=True,
                chunk_size=STREAMING_CHUNK_SIZE,
                encoder_chunk_look_back=4,
                decoder_chunk_look_back=1,
                disable_pbar=True,
            )
            self.reset_stream()
        else:
            self.model.generate(input=audio, batch_size_s=1, disable_pbar=True)

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        duration: float,
        hotwords: list[str] | None = None,
        final: bool = False,
        window_start: float = 0.0,
        revision: int | None = None,
    ) -> dict[str, Any]:
        if self.model is None:
            raise RuntimeError("Paraformer 模型尚未加载")
        if self.is_streaming:
            return self._transcribe_streaming(
                audio,
                duration=duration,
                final=final,
                window_start=window_start,
                revision=revision,
            )
        options: dict[str, Any] = {
            "input": audio,
            "batch_size_s": max(1, min(60, int(duration) + 1)),
            "disable_pbar": True,
        }
        if hotwords:
            # Paraformer variants with contextual decoding consume this field;
            # a text file keeps phrases containing spaces intact. Base models
            # safely ignore it; explicit corrections still run afterward.
            options["hotword"] = str(write_hotword_file(self.hotword_file, hotwords))
        generated = self.model.generate(**options)
        item = generated[0] if generated else {}
        text = str(item.get("text") or "").strip()
        segments = []
        if text:
            segments = [{"start": 0.0, "end": duration, "text": text}]
        return {
            "text": text,
            "language": "zh",
            "segments": segments,
            "timestamp": item.get("timestamp") or [],
            "streaming": False,
        }

    def reset_stream(self) -> None:
        self._stream_cache = {}
        self._stream_text = ""
        self._processed_until = 0.0

    def _transcribe_streaming(
        self,
        audio: np.ndarray,
        *,
        duration: float,
        final: bool,
        window_start: float,
        revision: int | None,
    ) -> dict[str, Any]:
        if self.model is None:
            raise RuntimeError("Paraformer 模型尚未加载")
        if final or revision in (None, 0, 1):
            self.reset_stream()
            window_start = 0.0

        window_end = window_start + duration
        if window_start > self._processed_until + 0.05:
            self.reset_stream()
            self._processed_until = window_start
        offset_seconds = max(0.0, self._processed_until - window_start)
        offset_samples = min(len(audio), int(round(offset_seconds * 16_000)))
        pending = audio[offset_samples:]

        for start in range(0, len(pending), STREAMING_CHUNK_SAMPLES):
            chunk = pending[start : start + STREAMING_CHUNK_SAMPLES]
            is_last = start + STREAMING_CHUNK_SAMPLES >= len(pending)
            generated = self.model.generate(
                input=chunk,
                cache=self._stream_cache,
                is_final=final and is_last,
                chunk_size=STREAMING_CHUNK_SIZE,
                encoder_chunk_look_back=4,
                decoder_chunk_look_back=1,
                disable_pbar=True,
            )
            item = generated[0] if generated else {}
            self._stream_text = _merge_stream_text(
                self._stream_text,
                str(item.get("text") or "").strip(),
            )
        self._processed_until = max(self._processed_until, window_end)
        text = self._stream_text.strip()
        return {
            "text": text,
            "language": "zh",
            "segments": [],
            "timestamp": [],
            "streaming": True,
        }


def _merge_stream_text(existing: str, update: str) -> str:
    if not update:
        return existing
    if not existing or update.startswith(existing):
        return update
    if existing.endswith(update):
        return existing
    overlap = min(len(existing), len(update))
    for size in range(overlap, 1, -1):
        if existing.endswith(update[:size]):
            return existing + update[size:]
    if existing[-1].isascii() and update[0].isascii():
        return existing + " " + update
    return existing + update
