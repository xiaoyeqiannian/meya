"""Reference reducer for the host-owned push-to-talk session lifecycle.

Swift and C# hosts implement this small machine natively. The JSON golden
traces exercise the same transitions so platform differences remain explicit
without introducing a cross-language FFI library into the input path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SessionState(str, Enum):
    IDLE = "idle"
    ARMING = "arming"
    RECORDING = "recording"
    OVERLAY_ONLY = "overlay_only"
    FINALIZING = "finalizing"
    COMMITTING = "committing"
    CANCELLING = "cancelling"


class Event(str, Enum):
    TRIGGER_PRESSED = "trigger_pressed"
    HOLD_ELAPSED = "hold_elapsed"
    TRIGGER_RELEASED = "trigger_released"
    TRIGGER_CANCELLED = "trigger_cancelled"
    PARTIAL = "partial"
    SINK_LOST = "sink_lost"
    SINK_AVAILABLE = "sink_available"
    FINAL = "final"
    RECOGNIZER_FAILED = "recognizer_failed"
    FINAL_TIMEOUT = "final_timeout"
    COMMIT_DONE = "commit_done"
    CANCEL_DONE = "cancel_done"


@dataclass(frozen=True)
class Transition:
    state: SessionState
    actions: tuple[str, ...] = ()


_TRANSITIONS: dict[tuple[SessionState, Event], Transition] = {
    (SessionState.IDLE, Event.TRIGGER_PRESSED): Transition(SessionState.ARMING, ("schedule_hold",)),
    (SessionState.ARMING, Event.TRIGGER_RELEASED): Transition(SessionState.IDLE, ("cancel_hold", "pass_through")),
    (SessionState.ARMING, Event.TRIGGER_CANCELLED): Transition(SessionState.IDLE, ("cancel_hold", "pass_through")),
    (SessionState.ARMING, Event.HOLD_ELAPSED): Transition(SessionState.RECORDING, ("begin_sink", "start_audio", "start_preview", "show_recording")),
    (SessionState.RECORDING, Event.PARTIAL): Transition(SessionState.RECORDING, ("update_draft",)),
    (SessionState.RECORDING, Event.SINK_LOST): Transition(SessionState.OVERLAY_ONLY, ("cancel_draft", "route_draft_to_overlay")),
    (SessionState.OVERLAY_ONLY, Event.PARTIAL): Transition(SessionState.OVERLAY_ONLY, ("update_overlay_draft",)),
    (SessionState.OVERLAY_ONLY, Event.SINK_AVAILABLE): Transition(SessionState.RECORDING, ("begin_sink", "update_draft")),
    (SessionState.RECORDING, Event.TRIGGER_RELEASED): Transition(SessionState.FINALIZING, ("stop_audio", "finalize_preview", "start_final", "show_recognizing")),
    (SessionState.OVERLAY_ONLY, Event.TRIGGER_RELEASED): Transition(SessionState.FINALIZING, ("stop_audio", "finalize_preview", "start_final", "show_recognizing")),
    (SessionState.RECORDING, Event.TRIGGER_CANCELLED): Transition(SessionState.CANCELLING, ("stop_audio", "cancel_preview", "cancel_draft")),
    (SessionState.OVERLAY_ONLY, Event.TRIGGER_CANCELLED): Transition(SessionState.CANCELLING, ("stop_audio", "cancel_preview", "cancel_draft")),
    (SessionState.FINALIZING, Event.FINAL): Transition(SessionState.COMMITTING, ("commit_final",)),
    (SessionState.FINALIZING, Event.RECOGNIZER_FAILED): Transition(SessionState.COMMITTING, ("commit_best_partial",)),
    (SessionState.FINALIZING, Event.FINAL_TIMEOUT): Transition(SessionState.COMMITTING, ("commit_best_partial", "restart_final_worker")),
    (SessionState.COMMITTING, Event.COMMIT_DONE): Transition(SessionState.IDLE, ("cleanup_session", "hide_overlay")),
    (SessionState.CANCELLING, Event.CANCEL_DONE): Transition(SessionState.IDLE, ("cleanup_session", "hide_overlay")),
}


def transition(state: SessionState, event: Event) -> Transition:
    """Return an explicit transition; irrelevant late events are ignored."""
    return _TRANSITIONS.get((state, event), Transition(state))
