using System.Buffers.Binary;
using NAudio.Wave;

namespace Meya.Windows;

internal sealed class StreamingPcm16Converter
{
    internal const int OutputSampleRate = 16_000;
    internal const int ChunkSamples = 7_680;

    private static readonly Guid PcmSubFormat = new("00000001-0000-0010-8000-00aa00389b71");
    private static readonly Guid FloatSubFormat = new("00000003-0000-0010-8000-00aa00389b71");

    private readonly int _inputSampleRate;
    private readonly int _channels;
    private readonly int _bitsPerSample;
    private readonly int _blockAlign;
    private readonly bool _floatingPoint;
    private readonly double _step;
    private readonly List<float> _source = [];
    private readonly List<short> _pending = [];
    private byte[] _remainder = [];
    private double _sourcePosition;

    internal float CurrentLevel { get; private set; } = 0.03f;

    internal StreamingPcm16Converter(WaveFormat format)
    {
        ArgumentNullException.ThrowIfNull(format);
        if (format.SampleRate <= 0 || format.Channels <= 0 || format.BlockAlign <= 0)
        {
            throw new InvalidDataException("无效的 WASAPI 音频格式");
        }

        _inputSampleRate = format.SampleRate;
        _channels = format.Channels;
        _bitsPerSample = format.BitsPerSample;
        _blockAlign = format.BlockAlign;
        _floatingPoint = format.Encoding switch
        {
            WaveFormatEncoding.IeeeFloat => true,
            WaveFormatEncoding.Pcm => false,
            WaveFormatEncoding.Extensible when format is WaveFormatExtensible extensible && extensible.SubFormat == FloatSubFormat => true,
            WaveFormatEncoding.Extensible when format is WaveFormatExtensible extensible && extensible.SubFormat == PcmSubFormat => false,
            _ => throw new NotSupportedException($"不支持的 WASAPI 音频编码：{format.Encoding}"),
        };
        if (_floatingPoint && _bitsPerSample is not (32 or 64))
        {
            throw new NotSupportedException($"不支持的浮点采样位数：{_bitsPerSample}");
        }
        if (!_floatingPoint && _bitsPerSample is not (16 or 24 or 32))
        {
            throw new NotSupportedException($"不支持的 PCM 采样位数：{_bitsPerSample}");
        }
        _step = (double)_inputSampleRate / OutputSampleRate;
    }

    internal IReadOnlyList<byte[]> Append(ReadOnlySpan<byte> input)
    {
        if (input.IsEmpty && _remainder.Length == 0)
        {
            return [];
        }

        byte[] combined = new byte[_remainder.Length + input.Length];
        _remainder.CopyTo(combined, 0);
        input.CopyTo(combined.AsSpan(_remainder.Length));
        int completeBytes = combined.Length - combined.Length % _blockAlign;
        _remainder = combined.AsSpan(completeBytes).ToArray();

        int sampleBytes = _bitsPerSample / 8;
        double sumSquares = 0;
        int frameCount = 0;
        for (int frameOffset = 0; frameOffset < completeBytes; frameOffset += _blockAlign)
        {
            float sum = 0;
            for (int channel = 0; channel < _channels; channel++)
            {
                int offset = frameOffset + channel * sampleBytes;
                sum += DecodeSample(combined.AsSpan(offset, sampleBytes));
            }
            float mono = sum / _channels;
            _source.Add(mono);
            sumSquares += mono * mono;
            frameCount++;
        }
        if (frameCount > 0)
        {
            double rms = Math.Sqrt(sumSquares / frameCount);
            double decibels = 20 * Math.Log10(Math.Max(rms, 0.000_01));
            CurrentLevel = (float)Math.Clamp((decibels + 55) / 45, 0, 1);
        }
        Resample(flush: false);
        return TakeCompleteChunks();
    }

    internal IReadOnlyList<byte[]> Flush()
    {
        Resample(flush: true);
        List<byte[]> chunks = [.. TakeCompleteChunks()];
        if (_pending.Count > 0)
        {
            chunks.Add(Encode(_pending));
            _pending.Clear();
        }
        _source.Clear();
        _remainder = [];
        _sourcePosition = 0;
        return chunks;
    }

    private float DecodeSample(ReadOnlySpan<byte> sample)
    {
        if (_floatingPoint)
        {
            return _bitsPerSample == 32
                ? BitConverter.Int32BitsToSingle(BinaryPrimitives.ReadInt32LittleEndian(sample))
                : (float)BitConverter.Int64BitsToDouble(BinaryPrimitives.ReadInt64LittleEndian(sample));
        }
        return _bitsPerSample switch
        {
            16 => BinaryPrimitives.ReadInt16LittleEndian(sample) / 32768f,
            24 => DecodePcm24(sample) / 8_388_608f,
            32 => BinaryPrimitives.ReadInt32LittleEndian(sample) / 2_147_483_648f,
            _ => 0,
        };
    }

    private static int DecodePcm24(ReadOnlySpan<byte> sample)
    {
        int value = sample[0] | sample[1] << 8 | sample[2] << 16;
        return (value & 0x00800000) != 0 ? value | unchecked((int)0xFF000000) : value;
    }

    private void Resample(bool flush)
    {
        int requiredLookahead = flush ? 0 : 1;
        while (_sourcePosition + requiredLookahead < _source.Count)
        {
            int lower = (int)_sourcePosition;
            int upper = Math.Min(lower + 1, _source.Count - 1);
            double fraction = _sourcePosition - lower;
            float value = (float)(_source[lower] + (_source[upper] - _source[lower]) * fraction);
            _pending.Add(FloatToPcm16(value));
            _sourcePosition += _step;
        }

        int consumed = Math.Min((int)_sourcePosition, Math.Max(0, _source.Count - 1));
        if (consumed > 0)
        {
            _source.RemoveRange(0, consumed);
            _sourcePosition -= consumed;
        }
        if (flush)
        {
            _source.Clear();
            _sourcePosition = 0;
        }
    }

    private IReadOnlyList<byte[]> TakeCompleteChunks()
    {
        List<byte[]> chunks = [];
        while (_pending.Count >= ChunkSamples)
        {
            chunks.Add(Encode(_pending.GetRange(0, ChunkSamples)));
            _pending.RemoveRange(0, ChunkSamples);
        }
        return chunks;
    }

    private static short FloatToPcm16(float value)
    {
        float clipped = Math.Clamp(value, -1f, 1f);
        return clipped <= -1f ? short.MinValue : (short)Math.Round(clipped * short.MaxValue);
    }

    private static byte[] Encode(IReadOnlyList<short> samples)
    {
        byte[] bytes = new byte[samples.Count * sizeof(short)];
        for (int index = 0; index < samples.Count; index++)
        {
            BinaryPrimitives.WriteInt16LittleEndian(bytes.AsSpan(index * sizeof(short), sizeof(short)), samples[index]);
        }
        return bytes;
    }
}
