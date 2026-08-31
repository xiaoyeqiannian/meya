import json
from pathlib import Path
import sys
from uuid import UUID, uuid4

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from meya_core.capabilities import HotwordMode, RecognizerCapabilities, StreamingMode  # noqa: E402
from meya_core.framing import (  # noqa: E402
    Frame,
    FrameDecoder,
    FrameType,
    ProtocolError,
    encode_frame,
    json_frame,
)
from meya_core.session_contract import Event, SessionState, transition  # noqa: E402
import asr_daemon as daemon  # noqa: E402


def test_frames_survive_fragmentation_and_coalescing():
    session = uuid4()
    expected = [
        json_frame(FrameType.CONTROL, {"command": "stream_start", "文本": "麦芽"}, session=session, sequence=7),
        Frame(FrameType.AUDIO_PCM16, b"\x00\x01\x02\x03", session=session, sequence=8),
    ]
    wire = b"noise from a dependency\n" + b"".join(encode_frame(frame) for frame in expected)
    decoder = FrameDecoder()
    actual = []
    for index in range(0, len(wire), 7):
        actual.extend(decoder.feed(wire[index : index + 7]))
    assert actual == expected
    assert decoder.discarded_bytes == len(b"noise from a dependency\n")
    assert actual[0].json()["文本"] == "麦芽"


def test_wire_format_matches_language_neutral_golden_fixtures():
    payload = json.loads((ROOT / "contracts/ipc-v2-fixtures.json").read_text(encoding="utf-8"))
    for fixture in payload["fixtures"]:
        raw_payload = (
            fixture["payload_utf8"].encode("utf-8")
            if "payload_utf8" in fixture
            else bytes.fromhex(fixture["payload_hex"])
        )
        frame = Frame(
            FrameType(fixture["type"]),
            raw_payload,
            fixture["flags"],
            UUID(fixture["session"]),
            fixture["sequence"],
        )
        assert encode_frame(frame).hex() == fixture["wire_hex"], fixture["name"]


def test_frame_rejects_unsupported_json_type():
    try:
        json_frame(FrameType.AUDIO_PCM16, {"bad": True})
    except ProtocolError:
        pass
    else:
        raise AssertionError("audio frames must not contain JSON")


def test_capabilities_are_model_agnostic():
    value = RecognizerCapabilities(
        backend="paraformer",
        model="local/model",
        role="preview",
        streaming=StreamingMode.NATIVE,
        hotwords=HotwordMode.NONE,
        punctuation=False,
        languages=("zh", "en"),
    ).as_dict()
    assert value["streaming_mode"] == "native"
    assert value["audio_transports"] == ["framed_pcm16"]
    assert value["protocol_versions"] == [2]


def test_every_platform_must_pass_shared_session_traces():
    payload = json.loads((ROOT / "contracts/session-traces.json").read_text(encoding="utf-8"))
    for trace in payload["traces"]:
        state = SessionState.IDLE
        states = [state.value]
        actions = []
        for raw_event in trace["events"]:
            result = transition(state, Event(raw_event))
            state = result.state
            states.append(state.value)
            actions.extend(result.actions)
        assert states == trace["states"], trace["name"]
        assert set(trace["required_actions"]).issubset(actions), trace["name"]


def test_daemon_dispatches_raw_pcm_without_base64():
    class FakeStreamingAdapter:
        is_streaming = True

        def reset_stream(self):
            pass

        def transcribe(self, audio, **_kwargs):
            assert audio.dtype == np.float32
            assert len(audio) == 4
            return {"text": "测试", "language": "zh", "streaming": True}

    original = (
        daemon.MODEL_BACKEND,
        daemon.MODEL_NAME,
        daemon.PARAFORMER_ADAPTER,
        daemon.SAFE_LIVE_DRAFT,
        daemon.is_untrusted_preview_text,
        daemon.load_glossary,
        daemon.apply_corrections,
    )
    session = uuid4()
    try:
        daemon.MODEL_BACKEND = "paraformer"
        daemon.MODEL_NAME = "paraformer:test-streaming"
        daemon.PARAFORMER_ADAPTER = FakeStreamingAdapter()
        daemon.SAFE_LIVE_DRAFT = True
        daemon.is_untrusted_preview_text = lambda *_args: False
        daemon.load_glossary = lambda *_args, **_kwargs: []
        daemon.apply_corrections = lambda text, _path: (text, [])
        daemon.dispatch_frame(json_frame(
            FrameType.CONTROL,
            {"command": "stream_start"},
            session=session,
            sequence=40,
        ))
        pcm = np.array([0, 1_000, -1_000, 32_767], dtype="<i2").tobytes()
        response, should_quit = daemon.dispatch_frame(Frame(
            FrameType.AUDIO_PCM16,
            pcm,
            session=session,
            sequence=41,
        ))
        assert should_quit is False
        assert response["id"] == 41
        assert response["text"] == "测试"
    finally:
        (
            daemon.MODEL_BACKEND,
            daemon.MODEL_NAME,
            daemon.PARAFORMER_ADAPTER,
            daemon.SAFE_LIVE_DRAFT,
            daemon.is_untrusted_preview_text,
            daemon.load_glossary,
            daemon.apply_corrections,
        ) = original
        daemon.STREAM_SESSION_ID = None
