"""Model-role helpers used for legacy config migration and pass routing."""

from __future__ import annotations

from pathlib import Path


PREVIEW_CANDIDATES: tuple[str, ...] = (
    "mlx-community/whisper-large-v3-turbo-4bit",
    "mlx-community/whisper-large-v3-turbo",
    "mlx-community/whisper-small-mlx",
    "mlx-community/whisper-base-mlx",
    "mlx-community/whisper-tiny",
)


def discover_cached_models(hub: Path) -> list[str]:
    if not hub.exists():
        return []
    found: list[str] = []
    for path in hub.iterdir():
        name = path.name
        if not name.startswith("models--"):
            continue
        parts = name.removeprefix("models--").split("--")
        if len(parts) < 2:
            continue
        found.append(parts[0] + "/" + "--".join(parts[1:]))
    return sorted(set(found))


def resolve_preview_model(final_model: str, cached: list[str]) -> str:
    """Choose a preview default only when an old config has no explicit role."""
    cached_set = set(cached)
    for candidate in PREVIEW_CANDIDATES:
        if candidate in cached_set:
            return candidate
    return final_model


def model_for_pass(is_final: bool, preview_model: str, final_model: str) -> str:
    return final_model if is_final else preview_model
