#!/usr/bin/env python3
"""Persistent offline MLX Whisper worker using JSON Lines over stdin/stdout."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import sys
import time

import numpy as np


PROJECT_DIR = Path(__file__).resolve().parent
MODEL_NAME = os.environ.get("LOCAL_VOICE_MODEL", "mlx-community/whisper-large-v3-turbo")
MODEL_ROLE = os.environ.get("LOCAL_VOICE_ROLE", "preview")
MODEL_HOME = PROJECT_DIR / "models" / "huggingface"
SAFE_LIVE_DRAFT = os.environ.get("LOCAL_VOICE_SAFE_INLINE_DRAFT") == "1"

os.environ.setdefault("HF_HOME", str(MODEL_HOME))
os.environ.setdefault("HF_HUB_CACHE", str(MODEL_HOME / "hub"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from asr_adapters import (  # noqa: E402
    ParaformerAdapter,
    resolve_catalog_adapter,
    split_model_identifier,
)
from feedback_learning import (  # noqa: E402
    list_learning_rules,
    process_feedback,
    rollback_learning_rule,
)
from feedback_store import FeedbackStore  # noqa: E402
from glossary import (  # noqa: E402
    GlossaryEntry,
    apply_glossary_corrections,
    compact_cjk_spaces,
    glossary_hotwords,
    load_glossary,
)
from hotword_selector import HotwordSelection, select_hotword_entries  # noqa: E402
from streaming_coordinator import should_rerun_full, stabilize  # noqa: E402
from transcribe import (  # noqa: E402
    apply_corrections,
    is_untrusted_preview_text,
    load_terms,
    load_wav,
    resolve_whisper_language,
    user_file,
)


MODEL_SOURCE: str | None = None
MODEL_BACKEND, MODEL_BACKEND_NAME = split_model_identifier(MODEL_NAME)
PARAFORMER_ADAPTER: ParaformerAdapter | None = None
HOTWORD_CATALOG_REPORT = PROJECT_DIR / "runtime" / "hotword-catalog-report.json"
LEARNING_STORE: FeedbackStore | None = None


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def prompt_for_terms(entries: list[GlossaryEntry] | None = None) -> str | None:
    terms = [entry.canonical for entry in entries] if entries is not None else selected_terms()
    if not terms:
        return None
    # Whisper 的上下文窗口有限。词库可以保存很多词，但只把排在最前、
    # 最常用的一组放进提示，避免超长提示反而降低普通语句的识别质量。
    selected: list[str] = []
    length = 0
    for term in terms:
        added = len(term) + (1 if selected else 0)
        if len(selected) >= 100 or length + added > 1_000:
            break
        selected.append(term)
        length += added
    return "以下是可能出现的专有名词，请保持原有写法：" + "、".join(selected) + "。"


def selected_terms() -> list[str]:
    glossary = load_glossary(user_file("glossary.tsv", fallback_in_project=False))
    terms = glossary_hotwords(glossary) if glossary else load_terms(user_file("terms.txt"))
    selected: list[str] = []
    length = 0
    for term in terms:
        added = len(term) + (1 if selected else 0)
        if len(selected) >= 100 or length + added > 1_000:
            break
        selected.append(term)
        length += added
    return selected


def learning_store() -> FeedbackStore:
    global LEARNING_STORE
    if LEARNING_STORE is None:
        LEARNING_STORE = FeedbackStore(user_file("glossary.tsv", fallback_in_project=False).parent)
    return LEARNING_STORE


def load_recent_terms() -> dict[str, float]:
    return learning_store().recent_terms()


def request_context(request: dict) -> str:
    return "\n".join(
        str(request.get(key) or "")
        for key in ("context_text",)
    )


def request_draft(request: dict) -> str:
    return "\n".join(
        str(request.get(key) or "")
        for key in ("committed_text", "last_hypothesis", "draft_text")
    )


def dynamic_selection(request: dict, entries: list[GlossaryEntry], limit: int) -> HotwordSelection:
    return select_hotword_entries(
        entries,
        context_text=request_context(request),
        draft_text=request_draft(request),
        app_name=str(request.get("app_name") or ""),
        app_bundle=str(request.get("app_bundle") or ""),
        recent_terms=load_recent_terms(),
        limit=limit,
    )


def selected_paraformer_hotwords(
    entries: list[GlossaryEntry],
    selection: HotwordSelection,
    limit: int = 16,
) -> list[str]:
    if selection.acoustic_entries and PARAFORMER_ADAPTER is not None:
        return PARAFORMER_ADAPTER.prepare_hotwords(
            list(selection.acoustic_entries),
            max_terms=limit,
            max_forms_per_entry=1,
        )
    return []


def requested_hotword_limit(request: dict) -> int:
    try:
        return max(0, min(24, int(request.get("hotword_limit", 16))))
    except (TypeError, ValueError):
        return 16


def configured_catalog_models() -> tuple[str, ...]:
    """Return locally configured final models in priority order."""
    candidates: list[str] = []
    if MODEL_BACKEND == "paraformer":
        candidates.append(MODEL_NAME)
    paths = [
        user_file("model-config.json", fallback_in_project=False),
        PROJECT_DIR / "model-config.json",
    ]
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        for key in ("final_model", "model"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
    return tuple(dict.fromkeys(candidates))


def refresh_hotword_catalog(request_id: int = 0) -> dict:
    glossary = load_glossary(user_file("glossary.tsv", fallback_in_project=False))
    adapter = (
        PARAFORMER_ADAPTER
        if PARAFORMER_ADAPTER is not None
        and PARAFORMER_ADAPTER.is_seaco
        and PARAFORMER_ADAPTER.seg_dict_path.is_file()
        else resolve_catalog_adapter(PROJECT_DIR, configured_catalog_models())
    )
    if adapter is None:
        return {
            "id": request_id,
            "event": "hotword_catalog_refreshed",
            "supported": False,
            "entries": len(glossary),
            "error": "未找到本地 SeACo seg_dict",
        }
    summary = adapter.refresh_hotword_catalog(glossary)
    return {
        "id": request_id,
        "event": "hotword_catalog_refreshed",
        **summary,
    }


def feedback_request(request: dict) -> dict:
    glossary_path = user_file("glossary.tsv", fallback_in_project=False)
    expected = str(request.get("expected_text") or "")[:8_000]
    edited = str(request.get("edited_text") or "")[:8_000]
    if not expected or not edited:
        raise ValueError("反馈缺少修改前或修改后文本")
    event = process_feedback(
        expected=expected,
        edited=edited,
        raw_text=str(request.get("raw_text") or ""),
        final_text=str(request.get("final_text") or ""),
        audio_path=str(request.get("audio_path") or ""),
        app_name=str(request.get("app_name") or ""),
        entries=load_glossary(glossary_path),
        glossary_path=glossary_path,
        user_data_dir=glossary_path.parent,
        explicit=bool(request.get("explicit", False)),
    )
    return {
        "id": int(request.get("id", 0)),
        "event": "feedback_processed",
        "accepted_unchanged": bool(event.get("accepted_unchanged")),
        "observed": event.get("observed", []),
        "activated": event.get("activated", []),
    }


def learning_rules_request(request_id: int) -> dict:
    glossary_path = user_file("glossary.tsv", fallback_in_project=False)
    return {
        "id": request_id,
        "event": "learning_rules_listed",
        "rules": list_learning_rules(glossary_path.parent),
    }


def rollback_learning_rule_request(request: dict) -> dict:
    glossary_path = user_file("glossary.tsv", fallback_in_project=False)
    event = rollback_learning_rule(
        rule_id=int(request.get("rule_id", 0)),
        entries=load_glossary(glossary_path),
        glossary_path=glossary_path,
        user_data_dir=glossary_path.parent,
    )
    return {
        "id": int(request.get("id", 0)),
        "event": "learning_rule_reverted",
        "rule_id": event["rule_id"],
    }


def _local_hub_snapshot(model: str) -> Path | None:
    slug = "models--" + model.replace("/", "--")
    hub = MODEL_HOME / "hub" / slug
    ref = hub / "refs" / "main"
    if ref.exists():
        snapshot = hub / "snapshots" / ref.read_text().strip()
        if snapshot.exists():
            return snapshot
    snapshots = hub / "snapshots"
    if snapshots.exists():
        found = [path for path in snapshots.iterdir() if path.is_dir()]
        if len(found) == 1:
            return found[0]
    return None


def resolve_model_source(model: str) -> str:
    """Resolve a cached repo/local directory and normalize common MLX weight names."""
    source = Path(model).expanduser()
    if not source.exists():
        try:
            from huggingface_hub import snapshot_download

            source = Path(snapshot_download(repo_id=model, local_files_only=True))
        except Exception:
            cached = _local_hub_snapshot(model)
            if cached is None:
                raise
            source = cached

    if (source / "weights.safetensors").exists() or (source / "weights.npz").exists():
        return str(source)

    alternate = source / "model.safetensors"
    if alternate.exists():
        digest = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:12]
        adapter = PROJECT_DIR / "models" / "adapters" / digest
        adapter.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            target_name = "weights.safetensors" if item.name == "model.safetensors" else item.name
            target = adapter / target_name
            if target.exists() or target.is_symlink():
                continue
            target.symlink_to(item.resolve(), target_is_directory=item.is_dir())
        return str(adapter)

    raise FileNotFoundError(
        f"模型目录缺少 weights.safetensors、weights.npz 或 model.safetensors: {source}"
    )


def warmup_model(model_source: str) -> None:
    """Compile encoder/decoder graphs before the first real utterance."""
    import mlx_whisper

    audio = np.zeros(16_000, dtype=np.float32)
    audio[:800] = 0.02
    mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=model_source,
        language="en",
        temperature=0.0,
        condition_on_previous_text=False,
        no_speech_threshold=0.0,
        verbose=None,
    )


def transcribe_request(request: dict) -> dict:
    request_id = int(request.get("id", 0))
    final = bool(request.get("final", False))
    audio_path = Path(str(request["audio_path"]))
    started = time.perf_counter()

    audio, duration = load_wav(audio_path)
    rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64))) if len(audio) else 0.0
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if rms < 0.008 and peak < 0.05:
        return {
            "id": request_id,
            "final": final,
            "text": "",
            "raw_text": "",
            "duration": duration,
            "elapsed": time.perf_counter() - started,
            "silence": True,
        }

    requested_language = request.get("language") or os.environ.get("LOCAL_VOICE_LANGUAGE", "auto")
    language = resolve_whisper_language(
        str(requested_language) if requested_language is not None else "auto"
    )
    glossary = load_glossary(user_file("glossary.tsv", fallback_in_project=False))
    selection = dynamic_selection(request, glossary, requested_hotword_limit(request))
    active_entries = list(selection.entries)
    hotwords_used: list[str] = []
    if MODEL_BACKEND == "paraformer":
        if PARAFORMER_ADAPTER is None:
            raise RuntimeError("Paraformer 模型尚未加载")
        if final and not bool(request.get("disable_hotwords", False)):
            hotwords_used = selected_paraformer_hotwords(
                glossary,
                selection,
                requested_hotword_limit(request),
            )
        result = PARAFORMER_ADAPTER.transcribe(
            audio,
            duration=duration,
            hotwords=hotwords_used,
            final=final,
            window_start=float(request.get("window_start") or 0.0),
            revision=int(request["revision"]) if request.get("revision") is not None else None,
        )
    else:
        import mlx_whisper

        if MODEL_SOURCE is None:
            raise RuntimeError("模型尚未加载")
        transcribe_options = {
            "path_or_hf_repo": MODEL_SOURCE,
            "task": "transcribe",
            "initial_prompt": prompt_for_terms(active_entries) if final else None,
            "temperature": 0.0,
            "condition_on_previous_text": final,
            "no_speech_threshold": 0.5,
            "verbose": None,
        }
        if language:
            transcribe_options["language"] = language
        result = mlx_whisper.transcribe(audio, **transcribe_options)
    detected_language = str(result.get("language") or "").strip() or None
    raw_text = result.get("text", "").strip()
    if not final and is_untrusted_preview_text(raw_text, duration, language):
        raw_text = ""
    window_start = request.get("window_start")
    committed_text = str(request.get("committed_text") or "")
    committed_end = float(request.get("committed_end") or 0.0)
    is_native_streaming = bool(result.get("streaming"))
    use_window = window_start is not None or (committed_text and not should_rerun_full(duration, committed_end))
    if is_native_streaming:
        committed_text = ""
        committed_end = 0.0
        last_hypothesis = raw_text
        tail_text = raw_text
    elif use_window:
        merged = stabilize(
            committed_text=committed_text,
            committed_end=committed_end,
            last_hypothesis=str(request.get("last_hypothesis") or ""),
            window_start=float(window_start or 0.0),
            window_end=float(window_start or 0.0) + duration,
            window_text=raw_text,
            segments=list(result.get("segments") or []),
        )
        raw_text = merged["display_text"]
        committed_text = merged["committed_text"]
        committed_end = merged["committed_end"]
        last_hypothesis = merged["last_hypothesis"]
        tail_text = merged["tail_text"]
    else:
        last_hypothesis = raw_text
        tail_text = ""
        if final:
            committed_text = raw_text
            committed_end = duration
    normalized_text = compact_cjk_spaces(raw_text)
    if bool(request.get("disable_corrections", False)):
        text = normalized_text
        changes: list[tuple[str, str]] = []
    else:
        evidenced = select_hotword_entries(
            glossary,
            draft_text=normalized_text,
            limit=requested_hotword_limit(request),
        )
        active_by_name = {entry.canonical.casefold(): entry for entry in active_entries}
        for entry in evidenced.entries:
            active_by_name.setdefault(entry.canonical.casefold(), entry)
        active_entries = list(active_by_name.values())
        if glossary:
            text, changes = apply_glossary_corrections(normalized_text, active_entries)
        else:
            text, changes = apply_corrections(normalized_text, user_file("corrections.tsv"))
    if final and changes:
        learning_store().record_rule_hits(changes)
    client_text = text if final or SAFE_LIVE_DRAFT else ""
    return {
        "id": request_id,
        "final": final,
        "text": client_text,
        "raw_text": raw_text,
        "duration": duration,
        "elapsed": time.perf_counter() - started,
        "silence": False,
        "model": MODEL_NAME,
        "language": detected_language,
        "committed_text": committed_text,
        "committed_end": committed_end,
        "tail_text": tail_text,
        "last_hypothesis": last_hypothesis,
        "revision": request.get("revision"),
        "hotwords_enabled": bool(hotwords_used),
        "hotwords_used": hotwords_used,
        "selected_terms": [entry.canonical for entry in active_entries],
        "selection_reasons": {
            key: list(value) for key, value in selection.reasons.items()
        },
        "corrections": [{"from": source, "to": target} for source, target in changes],
    }


def main() -> int:
    global MODEL_SOURCE, PARAFORMER_ADAPTER
    if "--refresh-catalog-only" in sys.argv[1:]:
        try:
            result = refresh_hotword_catalog()
            emit(result)
            return 0 if result.get("supported") else 1
        except Exception as exc:
            emit({"event": "fatal", "error": str(exc)})
            return 1
    try:
        if MODEL_BACKEND == "paraformer":
            PARAFORMER_ADAPTER = ParaformerAdapter(PROJECT_DIR, MODEL_BACKEND_NAME, role=MODEL_ROLE)
            PARAFORMER_ADAPTER.load()
            PARAFORMER_ADAPTER.warmup()
        else:
            import mlx.core as mx
            from mlx_whisper.transcribe import ModelHolder

            MODEL_SOURCE = resolve_model_source(MODEL_BACKEND_NAME)
            ModelHolder.get_model(MODEL_SOURCE, mx.float16)
            warmup_model(MODEL_SOURCE)
        if MODEL_ROLE == "final" or (
            PARAFORMER_ADAPTER is not None and PARAFORMER_ADAPTER.is_seaco
        ):
            refresh_hotword_catalog()
        emit({
            "event": "ready",
            "model": MODEL_NAME,
            "backend": MODEL_BACKEND,
            "role": MODEL_ROLE,
            "punctuation": bool(
                PARAFORMER_ADAPTER is not None and PARAFORMER_ADAPTER.punctuation_source is not None
            ),
        })
    except Exception as exc:
        emit({"event": "fatal", "error": str(exc)})
        return 1

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id = 0
        try:
            request = json.loads(line)
            request_id = int(request.get("id", 0))
            if request.get("command") == "quit":
                emit({"event": "bye"})
                return 0
            if request.get("command") == "feedback":
                emit(feedback_request(request))
                continue
            if request.get("command") == "list_learning_rules":
                emit(learning_rules_request(request_id))
                continue
            if request.get("command") == "rollback_learning_rule":
                emit(rollback_learning_rule_request(request))
                continue
            if request.get("command") == "refresh_hotword_catalog":
                emit(refresh_hotword_catalog(request_id))
                continue
            if request.get("command") != "transcribe":
                raise ValueError("不支持的命令")
            emit(transcribe_request(request))
        except Exception as exc:
            emit({"id": request_id, "error": str(exc)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
