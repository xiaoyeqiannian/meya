using System.Runtime.InteropServices;
using NAudio.CoreAudioApi;
using NAudio.Wave;

namespace Meya.Windows;

internal sealed record CapturedAudio(string Path, TimeSpan Duration, byte[] FinalPcm16);

internal sealed class AudioCapture : IAsyncDisposable
{
    private readonly object _sync = new();
    private WasapiCapture? _capture;
    private MMDevice? _device;
    private WaveFileWriter? _writer;
    private StreamingPcm16Converter? _streamConverter;
    private TaskCompletionSource<Exception?>? _stopped;
    private long _bytesWritten;
    private string? _path;

    internal event Action<byte[]>? Pcm16Available;

    internal void Start(string outputPath)
    {
        if (_capture is not null)
        {
            throw new InvalidOperationException("录音已经开始");
        }
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(outputPath)!);
        MMDevice device = SelectCaptureDevice();
        WasapiCapture capture;
        try
        {
            capture = new WasapiCapture(device);
        }
        catch
        {
            device.Dispose();
            throw;
        }

        StreamingPcm16Converter converter = new(capture.WaveFormat);
        WaveFileWriter writer = new(
            outputPath,
            new WaveFormat(StreamingPcm16Converter.OutputSampleRate, 16, 1));
        _capture = capture;
        _device = device;
        _writer = writer;
        _streamConverter = converter;
        _path = outputPath;
        _bytesWritten = 0;
        _stopped = new(TaskCreationOptions.RunContinuationsAsynchronously);
        capture.DataAvailable += OnDataAvailable;
        capture.RecordingStopped += OnRecordingStopped;
        try
        {
            RuntimeLog.Write($"Audio input selected name={device.FriendlyName} id={device.ID} captureFormat={capture.WaveFormat} outputFormat=16kHz mono PCM16");
            capture.StartRecording();
        }
        catch
        {
            capture.DataAvailable -= OnDataAvailable;
            capture.RecordingStopped -= OnRecordingStopped;
            writer.Dispose();
            capture.Dispose();
            device.Dispose();
            _capture = null;
            _device = null;
            _writer = null;
            _streamConverter = null;
            throw;
        }
    }

    internal async Task<CapturedAudio> StopAsync(CancellationToken cancellationToken = default)
    {
        WasapiCapture capture = _capture ?? throw new InvalidOperationException("录音尚未开始");
        TaskCompletionSource<Exception?> stopped = _stopped!;
        capture.StopRecording();
        Exception? error = await stopped.Task.WaitAsync(TimeSpan.FromSeconds(10), cancellationToken)
            .ConfigureAwait(false);
        if (error is not null)
        {
            throw new InvalidOperationException("麦克风录音失败", error);
        }

        byte[] finalPcm16 = [];
        lock (_sync)
        {
            if (_streamConverter is not null)
            {
                IReadOnlyList<byte[]> tail = _streamConverter.Flush();
                if (tail.Count > 0)
                {
                    int length = tail.Sum(chunk => chunk.Length);
                    finalPcm16 = new byte[length];
                    int offset = 0;
                    foreach (byte[] chunk in tail)
                    {
                        _writer?.Write(chunk, 0, chunk.Length);
                        _bytesWritten += chunk.Length;
                        chunk.CopyTo(finalPcm16, offset);
                        offset += chunk.Length;
                    }
                }
            }
            _writer?.Dispose();
            _writer = null;
        }

        string path = _path!;
        double seconds = (double)_bytesWritten /
            (StreamingPcm16Converter.OutputSampleRate * sizeof(short));
        Cleanup();
        return new CapturedAudio(path, TimeSpan.FromSeconds(seconds), finalPcm16);
    }

    private static MMDevice SelectCaptureDevice()
    {
        using MMDeviceEnumerator enumerator = new();
        List<string> failures = [];
        foreach (Role role in new[] { Role.Multimedia, Role.Console, Role.Communications })
        {
            try
            {
                MMDevice device = enumerator.GetDefaultAudioEndpoint(DataFlow.Capture, role);
                RuntimeLog.Write($"Audio default capture role={role} name={device.FriendlyName}");
                return device;
            }
            catch (COMException exception) when ((uint)exception.HResult == 0x80070490)
            {
                failures.Add(role.ToString());
            }
        }

        MMDeviceCollection active = enumerator.EnumerateAudioEndPoints(DataFlow.Capture, DeviceState.Active);
        if (active.Count > 0)
        {
            MMDevice device = active[0];
            RuntimeLog.Write($"Audio default capture missing roles=[{string.Join(',', failures)}]; fallback={device.FriendlyName}");
            return device;
        }
        throw new InvalidOperationException(
            "Windows 没有可用的录音输入端点。请在 设置 → 系统 → 声音 → 输入 中启用并选择麦克风。");
    }

    private void OnDataAvailable(object? sender, WaveInEventArgs args)
    {
        IReadOnlyList<byte[]> chunks;
        lock (_sync)
        {
            chunks = _streamConverter?.Append(args.Buffer.AsSpan(0, args.BytesRecorded)) ?? [];
            foreach (byte[] chunk in chunks)
            {
                _writer?.Write(chunk, 0, chunk.Length);
                _bytesWritten += chunk.Length;
            }
            _writer?.Flush();
        }
        Action<byte[]>? callback = Pcm16Available;
        if (callback is not null)
        {
            foreach (byte[] chunk in chunks)
            {
                callback(chunk);
            }
        }
    }

    private void OnRecordingStopped(object? sender, StoppedEventArgs args)
    {
        _stopped?.TrySetResult(args.Exception);
    }

    private void Cleanup()
    {
        WasapiCapture? capture = _capture;
        if (capture is not null)
        {
            capture.DataAvailable -= OnDataAvailable;
            capture.RecordingStopped -= OnRecordingStopped;
            capture.Dispose();
        }
        _capture = null;
        _device?.Dispose();
        _device = null;
        _writer?.Dispose();
        _writer = null;
        _streamConverter = null;
        _stopped = null;
    }

    public ValueTask DisposeAsync()
    {
        try
        {
            _capture?.StopRecording();
        }
        catch
        {
        }
        Cleanup();
        return ValueTask.CompletedTask;
    }
}
