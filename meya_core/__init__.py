"""Platform-neutral contracts shared by Meya desktop hosts and ASR workers."""

from .capabilities import RecognizerCapabilities, StreamingMode, HotwordMode
from .framing import Frame, FrameDecoder, FrameType, ProtocolError, encode_frame
from .session_contract import Event, SessionState, transition
from .ports import DraftMode, HostCapabilities, TextSinkCapabilities

__all__ = [
    "Event",
    "DraftMode",
    "Frame",
    "FrameDecoder",
    "FrameType",
    "HotwordMode",
    "HostCapabilities",
    "ProtocolError",
    "RecognizerCapabilities",
    "SessionState",
    "StreamingMode",
    "TextSinkCapabilities",
    "encode_frame",
    "transition",
]
