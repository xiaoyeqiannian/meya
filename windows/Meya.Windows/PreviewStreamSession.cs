using System.Threading.Channels;

namespace Meya.Windows;

internal sealed class PreviewStreamSession : IAsyncDisposable
{
    private static readonly TimeSpan ChunkTimeout = TimeSpan.FromSeconds(15);
    private static readonly TimeSpan ControlTimeout = TimeSpan.FromSeconds(5);

    private readonly AsrWorker _worker;
    private readonly Guid _session;
    private readonly Channel<byte[]> _audio;
    private readonly CancellationTokenSource _cancellation = new();
    private readonly Task _pump;
    private int _finished;

    internal event EventHandler<PreviewResult>? Partial;

    private PreviewStreamSession(AsrWorker worker, Guid session)
    {
        _worker = worker;
        _session = session;
        _audio = Channel.CreateUnbounded<byte[]>(new UnboundedChannelOptions
        {
            SingleReader = true,
            SingleWriter = false,
            AllowSynchronousContinuations = false,
        });
        _pump = PumpAsync();
    }

    internal static async Task<PreviewStreamSession> StartAsync(
        AsrWorker worker,
        Guid session,
        CancellationToken cancellationToken = default)
    {
        if (!worker.SupportsNativeStreaming)
        {
            throw new InvalidOperationException("实时模型不支持原生 PCM16 流式识别");
        }
        await worker.StartStreamAsync(session, ControlTimeout, cancellationToken).ConfigureAwait(false);
        return new PreviewStreamSession(worker, session);
    }

    internal bool TryEnqueue(byte[] pcm16)
    {
        if (pcm16.Length == 0 || Volatile.Read(ref _finished) != 0)
        {
            return false;
        }
        return _audio.Writer.TryWrite(pcm16);
    }

    internal async Task CompleteAsync(byte[]? finalPcm16 = null)
    {
        if (Interlocked.Exchange(ref _finished, 1) != 0)
        {
            return;
        }
        if (finalPcm16 is { Length: > 0 })
        {
            _audio.Writer.TryWrite(finalPcm16);
        }
        _audio.Writer.TryComplete();
        Exception? failure = null;
        try
        {
            await _pump.ConfigureAwait(false);
        }
        catch (Exception exception)
        {
            failure = exception;
        }
        try
        {
            await _worker.CancelStreamAsync(_session, ControlTimeout).ConfigureAwait(false);
        }
        catch when (failure is not null)
        {
        }
        if (failure is not null)
        {
            throw new InvalidOperationException("实时草稿识别失败", failure);
        }
    }

    internal async Task CancelAsync()
    {
        if (Interlocked.Exchange(ref _finished, 1) != 0)
        {
            return;
        }
        _cancellation.Cancel();
        _audio.Writer.TryComplete();
        try
        {
            await _pump.ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
        }
        catch
        {
        }
        try
        {
            await _worker.CancelStreamAsync(_session, ControlTimeout).ConfigureAwait(false);
        }
        catch
        {
        }
    }

    private async Task PumpAsync()
    {
        await foreach (byte[] chunk in _audio.Reader.ReadAllAsync(_cancellation.Token).ConfigureAwait(false))
        {
            PreviewResult result = await _worker.PushPcm16Async(
                chunk,
                _session,
                ChunkTimeout,
                _cancellation.Token).ConfigureAwait(false);
            Partial?.Invoke(this, result);
        }
    }

    public async ValueTask DisposeAsync()
    {
        await CancelAsync().ConfigureAwait(false);
        _cancellation.Dispose();
    }
}
