"""Versioned local IPC frames for native hosts and model workers.

Every message has the same fixed header. JSON control/events and raw PCM16
audio therefore never share an ambiguous byte stream, while stdin/stdout can
remain the worker transport on every desktop platform.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import json
import struct
from typing import BinaryIO, Iterable
from uuid import UUID


MAGIC = b"MEYA"
VERSION = 2
MAX_PAYLOAD = 16 * 1024 * 1024
HEADER = struct.Struct("!4sBBHI16sQ")
ZERO_SESSION = UUID(int=0)


class ProtocolError(ValueError):
    pass


class FrameType(IntEnum):
    CONTROL = 1
    AUDIO_PCM16 = 2
    EVENT = 3
    ERROR = 4
    HEARTBEAT = 5


@dataclass(frozen=True)
class Frame:
    kind: FrameType
    payload: bytes = b""
    flags: int = 0
    session: UUID = ZERO_SESSION
    sequence: int = 0
    version: int = VERSION

    def json(self) -> dict:
        if self.kind not in {FrameType.CONTROL, FrameType.EVENT, FrameType.ERROR}:
            raise ProtocolError(f"frame {self.kind.name} has no JSON payload")
        value = json.loads(self.payload.decode("utf-8"))
        if not isinstance(value, dict):
            raise ProtocolError("control payload must be a JSON object")
        return value


def encode_frame(frame: Frame) -> bytes:
    payload = bytes(frame.payload)
    if frame.version != VERSION:
        raise ProtocolError(f"unsupported protocol version: {frame.version}")
    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError("frame payload exceeds 16 MiB")
    if not 0 <= frame.flags <= 0xFFFF:
        raise ProtocolError("frame flags exceed uint16")
    if not 0 <= frame.sequence <= 0xFFFFFFFFFFFFFFFF:
        raise ProtocolError("frame sequence exceeds uint64")
    return HEADER.pack(
        MAGIC,
        frame.version,
        int(frame.kind),
        frame.flags,
        len(payload),
        frame.session.bytes,
        frame.sequence,
    ) + payload


def json_frame(
    kind: FrameType,
    value: dict,
    *,
    session: UUID = ZERO_SESSION,
    sequence: int = 0,
    flags: int = 0,
) -> Frame:
    if kind not in {FrameType.CONTROL, FrameType.EVENT, FrameType.ERROR}:
        raise ProtocolError("JSON is only valid for control, event, and error frames")
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return Frame(kind, payload, flags, session, sequence)


class FrameDecoder:
    """Incremental decoder with bounded resynchronization after stray stdout."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.discarded_bytes = 0

    def feed(self, data: bytes) -> list[Frame]:
        self._buffer.extend(data)
        frames: list[Frame] = []
        while True:
            if len(self._buffer) < len(MAGIC):
                break
            if self._buffer[: len(MAGIC)] != MAGIC:
                marker = self._buffer.find(MAGIC, 1)
                if marker < 0:
                    keep = min(len(MAGIC) - 1, len(self._buffer))
                    dropped = len(self._buffer) - keep
                    if dropped:
                        del self._buffer[:dropped]
                        self.discarded_bytes += dropped
                    break
                del self._buffer[:marker]
                self.discarded_bytes += marker
            if len(self._buffer) < HEADER.size:
                break
            magic, version, raw_kind, flags, size, raw_session, sequence = HEADER.unpack(
                self._buffer[: HEADER.size]
            )
            if magic != MAGIC:
                raise ProtocolError("invalid frame magic")
            if version != VERSION:
                raise ProtocolError(f"unsupported protocol version: {version}")
            if size > MAX_PAYLOAD:
                raise ProtocolError("frame payload exceeds 16 MiB")
            total = HEADER.size + size
            if len(self._buffer) < total:
                break
            try:
                kind = FrameType(raw_kind)
            except ValueError as error:
                raise ProtocolError(f"unknown frame type: {raw_kind}") from error
            payload = bytes(self._buffer[HEADER.size:total])
            del self._buffer[:total]
            frames.append(
                Frame(kind, payload, flags, UUID(bytes=raw_session), sequence, version)
            )
        return frames


def read_frames(stream: BinaryIO, chunk_size: int = 64 * 1024) -> Iterable[Frame]:
    decoder = FrameDecoder()
    # BufferedReader.read(n) may wait for all n bytes on a pipe. read1(n)
    # returns the bytes currently available, which keeps 480 ms audio frames
    # from waiting until a 64 KiB buffer fills.
    reader = getattr(stream, "read1", stream.read)
    while True:
        chunk = reader(chunk_size)
        if not chunk:
            return
        yield from decoder.feed(chunk)


def write_frame(stream: BinaryIO, frame: Frame) -> None:
    stream.write(encode_frame(frame))
    stream.flush()
