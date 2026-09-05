using System.Buffers.Binary;
using System.Text.Json;

namespace Meya.Core;

public enum MeyaFrameType : byte
{
    Control = 1,
    AudioPcm16 = 2,
    Event = 3,
    Error = 4,
    Heartbeat = 5,
}

public sealed record MeyaFrame(
    MeyaFrameType Type,
    byte[] Payload,
    ushort Flags = 0,
    Guid Session = default,
    ulong Sequence = 0);

public static class MeyaFrameCodec
{
    private static readonly byte[] Magic = "MEYA"u8.ToArray();
    public const byte Version = 2;
    public const int HeaderSize = 36;
    public const int MaximumPayload = 16 * 1024 * 1024;

    public static byte[] Encode(MeyaFrame frame)
    {
        ArgumentNullException.ThrowIfNull(frame.Payload);
        if (frame.Payload.Length > MaximumPayload)
        {
            throw new InvalidDataException("IPC payload exceeds 16 MiB");
        }

        byte[] data = new byte[HeaderSize + frame.Payload.Length];
        Magic.CopyTo(data, 0);
        data[4] = Version;
        data[5] = (byte)frame.Type;
        BinaryPrimitives.WriteUInt16BigEndian(data.AsSpan(6, 2), frame.Flags);
        BinaryPrimitives.WriteUInt32BigEndian(data.AsSpan(8, 4), (uint)frame.Payload.Length);
        GuidToNetworkBytes(frame.Session).CopyTo(data, 12);
        BinaryPrimitives.WriteUInt64BigEndian(data.AsSpan(28, 8), frame.Sequence);
        frame.Payload.CopyTo(data, HeaderSize);
        return data;
    }

    public static MeyaFrame Json(
        MeyaFrameType type,
        object value,
        Guid session = default,
        ulong sequence = 0)
    {
        if (type is not (MeyaFrameType.Control or MeyaFrameType.Event or MeyaFrameType.Error))
        {
            throw new InvalidDataException("JSON is only valid for control, event, and error frames");
        }
        return new MeyaFrame(type, JsonSerializer.SerializeToUtf8Bytes(value), 0, session, sequence);
    }

    public static JsonDocument ParseJson(MeyaFrame frame)
    {
        if (frame.Type is not (MeyaFrameType.Control or MeyaFrameType.Event or MeyaFrameType.Error))
        {
            throw new InvalidDataException($"Frame {frame.Type} has no JSON payload");
        }
        return JsonDocument.Parse(frame.Payload);
    }

    public static byte[] GuidToNetworkBytes(Guid value) => Convert.FromHexString(value.ToString("N"));

    public static Guid GuidFromNetworkBytes(ReadOnlySpan<byte> value)
    {
        if (value.Length != 16)
        {
            throw new ArgumentException("A UUID must contain 16 bytes", nameof(value));
        }
        return Guid.ParseExact(Convert.ToHexString(value), "N");
    }

    internal static ReadOnlySpan<byte> MagicBytes => Magic;
}

public sealed class MeyaFrameDecoder
{
    private readonly List<byte> _buffer = [];
    public long DiscardedBytes { get; private set; }

    public IReadOnlyList<MeyaFrame> Feed(ReadOnlySpan<byte> data)
    {
        if (!data.IsEmpty)
        {
            _buffer.AddRange(data.ToArray());
        }

        List<MeyaFrame> frames = [];
        while (true)
        {
            if (_buffer.Count < 4)
            {
                break;
            }

            int marker = FindMagic();
            if (marker < 0)
            {
                int keep = Math.Min(3, _buffer.Count);
                int drop = _buffer.Count - keep;
                if (drop > 0)
                {
                    _buffer.RemoveRange(0, drop);
                    DiscardedBytes += drop;
                }
                break;
            }
            if (marker > 0)
            {
                _buffer.RemoveRange(0, marker);
                DiscardedBytes += marker;
            }
            if (_buffer.Count < MeyaFrameCodec.HeaderSize)
            {
                break;
            }

            byte[] header = _buffer.GetRange(0, MeyaFrameCodec.HeaderSize).ToArray();
            if (header[4] != MeyaFrameCodec.Version)
            {
                throw new InvalidDataException($"Unsupported IPC version: {header[4]}");
            }
            if (!Enum.IsDefined(typeof(MeyaFrameType), header[5]))
            {
                throw new InvalidDataException($"Unknown IPC frame type: {header[5]}");
            }
            uint payloadSize = BinaryPrimitives.ReadUInt32BigEndian(header.AsSpan(8, 4));
            if (payloadSize > MeyaFrameCodec.MaximumPayload)
            {
                throw new InvalidDataException("IPC payload exceeds 16 MiB");
            }
            int total = checked(MeyaFrameCodec.HeaderSize + (int)payloadSize);
            if (_buffer.Count < total)
            {
                break;
            }

            byte[] payload = _buffer.GetRange(MeyaFrameCodec.HeaderSize, (int)payloadSize).ToArray();
            frames.Add(new MeyaFrame(
                (MeyaFrameType)header[5],
                payload,
                BinaryPrimitives.ReadUInt16BigEndian(header.AsSpan(6, 2)),
                MeyaFrameCodec.GuidFromNetworkBytes(header.AsSpan(12, 16)),
                BinaryPrimitives.ReadUInt64BigEndian(header.AsSpan(28, 8))));
            _buffer.RemoveRange(0, total);
        }
        return frames;
    }

    private int FindMagic()
    {
        ReadOnlySpan<byte> magic = MeyaFrameCodec.MagicBytes;
        for (int index = 0; index <= _buffer.Count - magic.Length; index++)
        {
            bool matches = true;
            for (int offset = 0; offset < magic.Length; offset++)
            {
                if (_buffer[index + offset] != magic[offset])
                {
                    matches = false;
                    break;
                }
            }
            if (matches)
            {
                return index;
            }
        }
        return -1;
    }
}
