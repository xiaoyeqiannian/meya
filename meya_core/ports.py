"""Language-neutral port semantics expressed as Python typing contracts.

These are reference contracts, not a runtime dependency for Swift or C#.
Native hosts mirror the same method and event semantics and validate behavior
with the shared session traces.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol
from uuid import UUID


class DraftMode(str, Enum):
    COMPOSITION = "composition"
    REPLACE_RANGE = "replace_range"
    OVERLAY_ONLY = "overlay_only"


@dataclass(frozen=True)
class TextSinkCapabilities:
    draft_mode: DraftMode
    can_commit: bool
    has_caret_bounds: bool = False


@dataclass(frozen=True)
class HostCapabilities:
    configurable_hold_trigger: bool
    native_composition: bool
    replace_range: bool
    exact_caret_bounds: bool
    per_monitor_overlay: bool


class TriggerSource(Protocol):
    def start(self, emit: Callable[[str, float], None]) -> None: ...
    def stop(self) -> None: ...


class AudioSource(Protocol):
    def start(self, session: UUID, emit_pcm16: Callable[[bytes, int], None]) -> None: ...
    def stop(self, session: UUID) -> None: ...


class TextSink(Protocol):
    def capabilities(self) -> TextSinkCapabilities: ...
    def set_external_termination_handler(
        self, emit: Callable[[UUID, str, str], None]
    ) -> None: ...
    def begin(self, session: UUID) -> str | None: ...
    def update_draft(
        self, session: UUID, composition_id: str, revision: int, text: str
    ) -> bool: ...
    def commit(
        self, session: UUID, composition_id: str, revision: int, text: str
    ) -> bool: ...
    def cancel(self, session: UUID, composition_id: str) -> None: ...


class OverlaySink(Protocol):
    def set_state(self, session: UUID, phase: str, meter: float, text: str = "") -> None: ...
    def hide(self, session: UUID) -> None: ...


class RecognizerSession(Protocol):
    def start(self, session: UUID, context: dict) -> None: ...
    def push_pcm16(self, session: UUID, sequence: int, payload: bytes) -> None: ...
    def finalize(self, session: UUID) -> None: ...
    def cancel(self, session: UUID) -> None: ...
