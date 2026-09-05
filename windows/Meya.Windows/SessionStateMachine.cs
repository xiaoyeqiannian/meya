namespace Meya.Windows;

internal enum SessionState
{
    Idle,
    Arming,
    Recording,
    OverlayOnly,
    Finalizing,
    Committing,
    Cancelling,
}

internal enum SessionEvent
{
    TriggerPressed,
    HoldElapsed,
    TriggerReleased,
    TriggerCancelled,
    Partial,
    SinkLost,
    SinkAvailable,
    Final,
    RecognizerFailed,
    FinalTimeout,
    CommitDone,
    CancelDone,
}

internal sealed record SessionTransition(SessionState State, params string[] Actions);

internal static class SessionStateMachine
{
    private static readonly IReadOnlyDictionary<(SessionState, SessionEvent), SessionTransition> Transitions =
        new Dictionary<(SessionState, SessionEvent), SessionTransition>
        {
            [(SessionState.Idle, SessionEvent.TriggerPressed)] = new(SessionState.Arming, "schedule_hold"),
            [(SessionState.Arming, SessionEvent.TriggerReleased)] = new(SessionState.Idle, "cancel_hold", "pass_through"),
            [(SessionState.Arming, SessionEvent.TriggerCancelled)] = new(SessionState.Idle, "cancel_hold", "pass_through"),
            [(SessionState.Arming, SessionEvent.HoldElapsed)] = new(SessionState.Recording, "begin_sink", "start_audio", "start_preview", "show_recording"),
            [(SessionState.Recording, SessionEvent.Partial)] = new(SessionState.Recording, "update_draft"),
            [(SessionState.Recording, SessionEvent.SinkLost)] = new(SessionState.OverlayOnly, "cancel_draft", "route_draft_to_overlay"),
            [(SessionState.OverlayOnly, SessionEvent.Partial)] = new(SessionState.OverlayOnly, "update_overlay_draft"),
            [(SessionState.OverlayOnly, SessionEvent.SinkAvailable)] = new(SessionState.Recording, "begin_sink", "update_draft"),
            [(SessionState.Recording, SessionEvent.TriggerReleased)] = new(SessionState.Finalizing, "stop_audio", "finalize_preview", "start_final", "show_recognizing"),
            [(SessionState.OverlayOnly, SessionEvent.TriggerReleased)] = new(SessionState.Finalizing, "stop_audio", "finalize_preview", "start_final", "show_recognizing"),
            [(SessionState.Recording, SessionEvent.TriggerCancelled)] = new(SessionState.Cancelling, "stop_audio", "cancel_preview", "cancel_draft"),
            [(SessionState.OverlayOnly, SessionEvent.TriggerCancelled)] = new(SessionState.Cancelling, "stop_audio", "cancel_preview", "cancel_draft"),
            [(SessionState.Finalizing, SessionEvent.Final)] = new(SessionState.Committing, "commit_final"),
            [(SessionState.Finalizing, SessionEvent.RecognizerFailed)] = new(SessionState.Committing, "commit_best_partial"),
            [(SessionState.Finalizing, SessionEvent.FinalTimeout)] = new(SessionState.Committing, "commit_best_partial", "restart_final_worker"),
            [(SessionState.Committing, SessionEvent.CommitDone)] = new(SessionState.Idle, "cleanup_session", "hide_overlay"),
            [(SessionState.Cancelling, SessionEvent.CancelDone)] = new(SessionState.Idle, "cleanup_session", "hide_overlay"),
        };

    internal static SessionTransition Transition(SessionState state, SessionEvent @event) =>
        Transitions.TryGetValue((state, @event), out SessionTransition? transition)
            ? transition
            : new SessionTransition(state);

    internal static string WireName(SessionState state) => state switch
    {
        SessionState.Idle => "idle",
        SessionState.Arming => "arming",
        SessionState.Recording => "recording",
        SessionState.OverlayOnly => "overlay_only",
        SessionState.Finalizing => "finalizing",
        SessionState.Committing => "committing",
        SessionState.Cancelling => "cancelling",
        _ => throw new ArgumentOutOfRangeException(nameof(state)),
    };

    internal static SessionEvent ParseEvent(string value) => value switch
    {
        "trigger_pressed" => SessionEvent.TriggerPressed,
        "hold_elapsed" => SessionEvent.HoldElapsed,
        "trigger_released" => SessionEvent.TriggerReleased,
        "trigger_cancelled" => SessionEvent.TriggerCancelled,
        "partial" => SessionEvent.Partial,
        "sink_lost" => SessionEvent.SinkLost,
        "sink_available" => SessionEvent.SinkAvailable,
        "final" => SessionEvent.Final,
        "recognizer_failed" => SessionEvent.RecognizerFailed,
        "final_timeout" => SessionEvent.FinalTimeout,
        "commit_done" => SessionEvent.CommitDone,
        "cancel_done" => SessionEvent.CancelDone,
        _ => throw new InvalidDataException($"Unknown session event: {value}"),
    };
}
