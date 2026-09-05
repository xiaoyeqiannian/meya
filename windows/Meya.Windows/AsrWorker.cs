using System.Collections.Concurrent;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;

namespace Meya.Windows;

internal sealed record TranscriptionResult(string Text, string RawText, bool Silence, string Model);
internal sealed record PreviewResult(string Text, string RawText, bool Silence, int Revision, string Model);

internal sealed class AsrWorker : IAsyncDisposable
{
    private readonly string _projectRoot;
    private readonly string _model;
    private readonly string _role;
    private readonly string _userDataDirectory;
    private readonly string _runtimeDirectory;
    private readonly ConcurrentDictionary<ulong, TaskCompletionSource<JsonElement>> _pending = new();
    private readonly SemaphoreSlim _writeLock = new(1, 1);
    private readonly CancellationTokenSource _lifetime = new();
    private readonly TaskCompletionSource<string> _ready = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private readonly MeyaFrameDecoder _decoder = new();

    private Process? _process;
    private Task? _stdoutTask;
    private Task? _stderrTask;
    private ulong _nextSequence;
    private int _stopping;

    internal event EventHandler<string>? Fatal;
    internal bool IsReady => _ready.Task.IsCompletedSuccessfully && _process is { HasExited: false };
    internal bool SupportsNativeStreaming { get; private set; }
    internal int? ProcessId => _process is { HasExited: false } process ? process.Id : null;

    internal AsrWorker(
        string projectRoot,
        string model,
        string role,
        string userDataDirectory,
        string runtimeDirectory)
    {
        _projectRoot = projectRoot;
        _model = model;
        _role = role;
        _userDataDirectory = userDataDirectory;
        _runtimeDirectory = runtimeDirectory;
    }

    internal async Task StartAsync(TimeSpan timeout, CancellationToken cancellationToken = default)
    {
        if (_process is not null)
        {
            throw new InvalidOperationException("识别服务已经启动");
        }

        string python = Path.Combine(_projectRoot, ".venv", "Scripts", "python.exe");
        string daemon = Path.Combine(_projectRoot, "asr_daemon.py");
        if (!File.Exists(python))
        {
            throw new FileNotFoundException("缺少 Windows Python 环境，请先运行 bootstrap_windows.ps1", python);
        }
        if (!File.Exists(daemon))
        {
            throw new FileNotFoundException("找不到 asr_daemon.py", daemon);
        }

        Directory.CreateDirectory(_userDataDirectory);
        Directory.CreateDirectory(_runtimeDirectory);
        ProcessStartInfo startInfo = new()
        {
            FileName = python,
            WorkingDirectory = _projectRoot,
            UseShellExecute = false,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardErrorEncoding = Encoding.UTF8,
            CreateNoWindow = true,
        };
        startInfo.ArgumentList.Add("-u");
        startInfo.ArgumentList.Add(daemon);
        string modelHome = Path.Combine(_projectRoot, "models", "huggingface");
        startInfo.Environment["PYTHONIOENCODING"] = "utf-8";
        startInfo.Environment["LOCAL_VOICE_MODEL"] = _model;
        startInfo.Environment["LOCAL_VOICE_ROLE"] = _role;
        startInfo.Environment["LOCAL_VOICE_SAFE_INLINE_DRAFT"] = "1";
        startInfo.Environment["MEYA_USER_DATA"] = _userDataDirectory;
        startInfo.Environment["MEYA_RUNTIME_DIR"] = _runtimeDirectory;
        startInfo.Environment["HF_HOME"] = modelHome;
        startInfo.Environment["HF_HUB_CACHE"] = Path.Combine(modelHome, "hub");
        startInfo.Environment["HF_HUB_OFFLINE"] = "1";
        startInfo.Environment["HF_DATASETS_OFFLINE"] = "1";
        startInfo.Environment["TRANSFORMERS_OFFLINE"] = "1";
        startInfo.Environment["TOKENIZERS_PARALLELISM"] = "false";

        Process process = new() { StartInfo = startInfo, EnableRaisingEvents = true };
        process.Exited += OnExited;
        _process = process;
        try
        {
            if (!process.Start())
            {
                throw new InvalidOperationException("无法启动识别服务");
            }
            RuntimeLog.Write($"ASR worker started pid={process.Id} role={_role} model={_model}");
            _stdoutTask = ReadStdoutAsync(process.StandardOutput.BaseStream, _lifetime.Token);
            _stderrTask = ReadStderrAsync(process.StandardError, _lifetime.Token);
            await _ready.Task.WaitAsync(timeout, cancellationToken).ConfigureAwait(false);
        }
        catch
        {
            await StopAsync().ConfigureAwait(false);
            throw;
        }
    }

    internal async Task<TranscriptionResult> TranscribeFinalAsync(
        string audioPath,
        Guid session,
        TimeSpan timeout,
        CancellationToken cancellationToken = default)
    {
        JsonElement response = await SendRequestAsync(
            new Dictionary<string, object?>
            {
                ["command"] = "transcribe",
                ["audio_path"] = audioPath,
                ["final"] = true,
            },
            session,
            timeout,
            cancellationToken).ConfigureAwait(false);
        ThrowIfError(response);
        return new TranscriptionResult(
            GetString(response, "text") ?? string.Empty,
            GetString(response, "raw_text") ?? string.Empty,
            GetBoolean(response, "silence"),
            GetString(response, "model") ?? _model);
    }

    internal async Task StartStreamAsync(
        Guid session,
        TimeSpan timeout,
        CancellationToken cancellationToken = default)
    {
        JsonElement response = await SendRequestAsync(
            new Dictionary<string, object?>
            {
                ["command"] = "stream_start",
                ["session"] = session,
            },
            session,
            timeout,
            cancellationToken).ConfigureAwait(false);
        ThrowIfError(response);
        if (!string.Equals(GetString(response, "event"), "stream_started", StringComparison.Ordinal))
        {
            throw new InvalidDataException("实时识别服务未确认流式会话");
        }
    }

    internal async Task<PreviewResult> PushPcm16Async(
        byte[] pcm16,
        Guid session,
        TimeSpan timeout,
        CancellationToken cancellationToken = default)
    {
        if (pcm16.Length == 0)
        {
            throw new ArgumentException("PCM16 音频帧不能为空", nameof(pcm16));
        }
        JsonElement response = await SendAudioAsync(pcm16, session, timeout, cancellationToken)
            .ConfigureAwait(false);
        ThrowIfError(response);
        return new PreviewResult(
            GetString(response, "text") ?? string.Empty,
            GetString(response, "raw_text") ?? string.Empty,
            GetBoolean(response, "silence"),
            GetInt32(response, "revision"),
            GetString(response, "model") ?? _model);
    }

    internal async Task CancelStreamAsync(
        Guid session,
        TimeSpan timeout,
        CancellationToken cancellationToken = default)
    {
        JsonElement response = await SendRequestAsync(
            new Dictionary<string, object?>
            {
                ["command"] = "stream_cancel",
                ["session"] = session,
            },
            session,
            timeout,
            cancellationToken).ConfigureAwait(false);
        ThrowIfError(response);
    }

    internal async Task<JsonElement> SendRequestAsync(
        IDictionary<string, object?> request,
        Guid session,
        TimeSpan timeout,
        CancellationToken cancellationToken = default)
    {
        if (!IsReady)
        {
            throw new InvalidOperationException("识别服务尚未就绪");
        }
        ulong sequence = unchecked(++_nextSequence);
        request["id"] = sequence;
        TaskCompletionSource<JsonElement> completion = RegisterPending(sequence);
        try
        {
            MeyaFrame frame = new(
                MeyaFrameType.Control,
                JsonSerializer.SerializeToUtf8Bytes(request),
                Session: session,
                Sequence: sequence);
            await WriteFrameAsync(frame, cancellationToken).ConfigureAwait(false);
            return await completion.Task.WaitAsync(timeout, cancellationToken).ConfigureAwait(false);
        }
        catch
        {
            _pending.TryRemove(sequence, out _);
            throw;
        }
    }

    private async Task<JsonElement> SendAudioAsync(
        byte[] pcm16,
        Guid session,
        TimeSpan timeout,
        CancellationToken cancellationToken)
    {
        if (!IsReady)
        {
            throw new InvalidOperationException("识别服务尚未就绪");
        }
        ulong sequence = unchecked(++_nextSequence);
        TaskCompletionSource<JsonElement> completion = RegisterPending(sequence);
        try
        {
            await WriteFrameAsync(new MeyaFrame(
                MeyaFrameType.AudioPcm16,
                pcm16,
                Session: session,
                Sequence: sequence), cancellationToken).ConfigureAwait(false);
            return await completion.Task.WaitAsync(timeout, cancellationToken).ConfigureAwait(false);
        }
        catch
        {
            _pending.TryRemove(sequence, out _);
            throw;
        }
    }

    private TaskCompletionSource<JsonElement> RegisterPending(ulong sequence)
    {
        TaskCompletionSource<JsonElement> completion = new(TaskCreationOptions.RunContinuationsAsynchronously);
        if (!_pending.TryAdd(sequence, completion))
        {
            throw new InvalidOperationException("无法创建识别请求");
        }
        return completion;
    }

    private async Task WriteFrameAsync(MeyaFrame frame, CancellationToken cancellationToken)
    {
        Process process = _process ?? throw new InvalidOperationException("识别服务未启动");
        if (process.HasExited)
        {
            throw new IOException($"识别服务已退出（{process.ExitCode}）");
        }
        byte[] bytes = MeyaFrameCodec.Encode(frame);
        await _writeLock.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            await process.StandardInput.BaseStream.WriteAsync(bytes, cancellationToken).ConfigureAwait(false);
            await process.StandardInput.BaseStream.FlushAsync(cancellationToken).ConfigureAwait(false);
        }
        finally
        {
            _writeLock.Release();
        }
    }

    private async Task ReadStdoutAsync(Stream stream, CancellationToken cancellationToken)
    {
        byte[] buffer = new byte[64 * 1024];
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                int count = await stream.ReadAsync(buffer, cancellationToken).ConfigureAwait(false);
                if (count == 0)
                {
                    break;
                }
                long before = _decoder.DiscardedBytes;
                foreach (MeyaFrame frame in _decoder.Feed(buffer.AsSpan(0, count)))
                {
                    HandleFrame(frame);
                }
                if (_decoder.DiscardedBytes != before)
                {
                    RuntimeLog.Write($"ASR stdout discarded {_decoder.DiscardedBytes - before} non-protocol bytes");
                }
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
        catch (Exception exception)
        {
            FailAll(exception);
            ReportFatal($"读取识别服务输出失败：{exception.Message}");
        }
    }

    private async Task ReadStderrAsync(StreamReader reader, CancellationToken cancellationToken)
    {
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                string? line = await reader.ReadLineAsync(cancellationToken).ConfigureAwait(false);
                if (line is null)
                {
                    break;
                }
                if (!string.IsNullOrWhiteSpace(line))
                {
                    RuntimeLog.Write($"ASR stderr: {line}");
                }
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
        catch (Exception exception)
        {
            RuntimeLog.Write($"ASR stderr reader failed: {exception.Message}");
        }
    }

    private void HandleFrame(MeyaFrame frame)
    {
        if (frame.Type is not (MeyaFrameType.Event or MeyaFrameType.Error))
        {
            return;
        }
        using JsonDocument document = MeyaFrameCodec.ParseJson(frame);
        JsonElement response = document.RootElement.Clone();
        string? eventName = GetString(response, "event");
        if (eventName == "ready")
        {
            string model = GetString(response, "model") ?? _model;
            SupportsNativeStreaming = GetBoolean(response, "streaming") ||
                response.TryGetProperty("capabilities", out JsonElement capabilities) &&
                string.Equals(GetString(capabilities, "streaming_mode"), "native", StringComparison.Ordinal);
            RuntimeLog.Write($"ASR worker ready pid={ProcessId} role={_role} model={model} nativeStreaming={SupportsNativeStreaming}");
            _ready.TrySetResult(model);
            return;
        }
        if (eventName == "fatal")
        {
            string message = GetString(response, "error") ?? "识别服务发生未知致命错误";
            InvalidOperationException exception = new(message);
            _ready.TrySetException(exception);
            FailAll(exception);
            ReportFatal(message);
            return;
        }
        if (response.TryGetProperty("id", out JsonElement id) &&
            id.TryGetUInt64(out ulong sequence) &&
            _pending.TryRemove(sequence, out TaskCompletionSource<JsonElement>? completion))
        {
            completion.TrySetResult(response);
        }
    }

    private void OnExited(object? sender, EventArgs args)
    {
        if (Volatile.Read(ref _stopping) != 0)
        {
            return;
        }
        int exitCode = sender is Process process ? process.ExitCode : -1;
        IOException exception = new($"识别服务意外退出（{exitCode}）");
        RuntimeLog.Write(exception.Message);
        _ready.TrySetException(exception);
        FailAll(exception);
        ReportFatal(exception.Message);
    }

    private void FailAll(Exception exception)
    {
        foreach ((ulong sequence, TaskCompletionSource<JsonElement> completion) in _pending)
        {
            if (_pending.TryRemove(sequence, out _))
            {
                completion.TrySetException(exception);
            }
        }
    }

    private void ReportFatal(string message)
    {
        try
        {
            Fatal?.Invoke(this, message);
        }
        catch
        {
        }
    }

    internal async Task StopAsync()
    {
        if (Interlocked.Exchange(ref _stopping, 1) != 0)
        {
            return;
        }
        Process? process = _process;
        if (process is not null && !process.HasExited)
        {
            try
            {
                ulong sequence = unchecked(++_nextSequence);
                await WriteFrameAsync(MeyaFrameCodec.Json(
                    MeyaFrameType.Control,
                    new { command = "quit", id = sequence },
                    sequence: sequence), CancellationToken.None).ConfigureAwait(false);
                await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(5)).ConfigureAwait(false);
            }
            catch
            {
                if (!process.HasExited)
                {
                    try
                    {
                        process.Kill(entireProcessTree: true);
                    }
                    catch
                    {
                    }
                }
            }
        }
        _lifetime.Cancel();
        FailAll(new OperationCanceledException("识别服务已停止"));
        if (process is not null)
        {
            process.Exited -= OnExited;
            process.Dispose();
        }
        _process = null;
    }

    public async ValueTask DisposeAsync()
    {
        await StopAsync().ConfigureAwait(false);
        _lifetime.Dispose();
        _writeLock.Dispose();
    }

    private static void ThrowIfError(JsonElement response)
    {
        string? error = GetString(response, "error");
        if (!string.IsNullOrWhiteSpace(error))
        {
            throw new InvalidOperationException(error);
        }
    }

    private static string? GetString(JsonElement root, string name) =>
        root.TryGetProperty(name, out JsonElement value) && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    private static bool GetBoolean(JsonElement root, string name) =>
        root.TryGetProperty(name, out JsonElement value) && value.ValueKind == JsonValueKind.True;

    private static int GetInt32(JsonElement root, string name) =>
        root.TryGetProperty(name, out JsonElement value) && value.TryGetInt32(out int result)
            ? result
            : 0;
}
