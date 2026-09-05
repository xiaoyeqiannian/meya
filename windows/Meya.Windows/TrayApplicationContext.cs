using System.Diagnostics;

namespace Meya.Windows;

internal sealed class TrayApplicationContext : ApplicationContext
{
    private static readonly TimeSpan HoldThreshold = TimeSpan.FromMilliseconds(350);
    private static readonly TimeSpan StartupTimeout = TimeSpan.FromMinutes(3);
    private static readonly TimeSpan RecognitionTimeout = TimeSpan.FromSeconds(60);

    private readonly SynchronizationContext _ui;
    private readonly string _projectRoot;
    private readonly string _userDataDirectory;
    private readonly string _runtimeDirectory;
    private readonly string _recordingsDirectory;
    private readonly ModelSelection _models;
    private readonly NotifyIcon _notifyIcon;
    private readonly OverlayForm _overlay = new();
    private readonly GlobalRightControl _trigger;
    private readonly System.Windows.Forms.Timer _holdTimer;

    private AsrWorker? _worker;
    private AsrWorker? _previewWorker;
    private PreviewStreamSession? _previewSession;
    private AudioCapture? _audio;
    private ForegroundTarget? _target;
    private Guid _session;
    private SessionState _state = SessionState.Idle;
    private string _bestPartial = string.Empty;
    private int _lastPreviewRevision;
    private bool _exiting;
    private bool _restarting;
    private bool _previewRestarting;

    internal TrayApplicationContext()
    {
        _ui = SynchronizationContext.Current ?? new WindowsFormsSynchronizationContext();
        _projectRoot = ProjectLocator.Locate();
        _userDataDirectory = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Meya");
        _runtimeDirectory = Path.Combine(_userDataDirectory, "runtime");
        _recordingsDirectory = Path.Combine(_userDataDirectory, "recordings", "voice-input");
        Directory.CreateDirectory(_recordingsDirectory);
        RuntimeLog.Configure(_runtimeDirectory);
        _models = ModelSelection.Load(_projectRoot, _userDataDirectory);

        ContextMenuStrip menu = new();
        ToolStripMenuItem status = new("麦芽 Meya · Windows IPC v2 Streaming") { Enabled = false };
        ToolStripMenuItem openLog = new("打开诊断日志");
        openLog.Click += (_, _) => OpenLog();
        ToolStripMenuItem exit = new("退出");
        exit.Click += async (_, _) => await ShutdownAsync();
        menu.Items.Add(status);
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add(openLog);
        menu.Items.Add(exit);

        _notifyIcon = new NotifyIcon
        {
            Icon = SystemIcons.Information,
            Text = "麦芽 Meya 正在加载识别模型",
            ContextMenuStrip = menu,
            Visible = true,
        };
        _holdTimer = new System.Windows.Forms.Timer { Interval = (int)HoldThreshold.TotalMilliseconds };
        _holdTimer.Tick += OnHoldElapsed;

        _trigger = new GlobalRightControl();
        _trigger.Pressed += (_, _) => _ui.Post(_ => OnTriggerPressed(), null);
        _trigger.Released += (_, _) => _ui.Post(async _ => await OnTriggerReleasedAsync(), null);
        _trigger.Cancelled += (_, _) => _ui.Post(async _ => await OnTriggerCancelledAsync(), null);

        RuntimeLog.Write($"Windows host started root={_projectRoot}");
        _ = StartWorkersAsync();
    }

    private async Task StartWorkersAsync()
    {
        await Task.WhenAll(StartFinalWorkerAsync(), StartPreviewWorkerAsync()).ConfigureAwait(true);
    }

    private async Task StartFinalWorkerAsync()
    {
        AsrWorker worker = new(
            _projectRoot,
            _models.Final,
            "final",
            _userDataDirectory,
            _runtimeDirectory);
        worker.Fatal += OnWorkerFatal;
        _worker = worker;
        try
        {
            await worker.StartAsync(StartupTimeout).ConfigureAwait(true);
            if (_worker != worker || _exiting)
            {
                await worker.DisposeAsync();
                return;
            }
            UpdateReadyStatus();
            _overlay.ShowState("麦芽已就绪 · 按住右 Ctrl 说话");
            await Task.Delay(1000);
            if (_state == SessionState.Idle)
            {
                _overlay.HideState();
            }
        }
        catch (Exception exception)
        {
            if (_worker == worker)
            {
                _worker = null;
            }
            worker.Fatal -= OnWorkerFatal;
            await worker.DisposeAsync();
            ShowError("最终识别服务启动失败", exception.Message);
            _overlay.ShowState("最终模型启动失败 · 查看诊断日志");
        }
    }

    private async Task StartPreviewWorkerAsync()
    {
        AsrWorker worker = new(
            _projectRoot,
            _models.Preview,
            "preview",
            _userDataDirectory,
            _runtimeDirectory);
        worker.Fatal += OnPreviewWorkerFatal;
        _previewWorker = worker;
        try
        {
            await worker.StartAsync(StartupTimeout).ConfigureAwait(true);
            if (_previewWorker != worker || _exiting)
            {
                await worker.DisposeAsync();
                return;
            }
            if (!worker.SupportsNativeStreaming)
            {
                throw new InvalidOperationException("当前 preview 模型不支持原生 Paraformer Streaming");
            }
            RuntimeLog.Write($"Preview streaming ready pid={worker.ProcessId} model={_models.Preview}");
            UpdateReadyStatus();
        }
        catch (Exception exception)
        {
            RuntimeLog.Write($"Preview worker unavailable: {exception}");
            if (_previewWorker == worker)
            {
                _previewWorker = null;
            }
            worker.Fatal -= OnPreviewWorkerFatal;
            await worker.DisposeAsync();
            UpdateReadyStatus();
        }
    }

    private void UpdateReadyStatus()
    {
        if (_worker is not { IsReady: true })
        {
            _notifyIcon.Text = "麦芽 Meya 正在加载最终模型";
            return;
        }
        _notifyIcon.Text = _previewWorker is { IsReady: true, SupportsNativeStreaming: true }
            ? "麦芽 Meya · 实时草稿已就绪"
            : "麦芽 Meya · 最终定稿已就绪";
    }

    private void OnTriggerPressed()
    {
        if (_exiting || _state != SessionState.Idle)
        {
            return;
        }
        if (_worker is not { IsReady: true })
        {
            ShowError("最终模型尚未就绪", "请等待模型加载完成后再试");
            return;
        }
        Apply(SessionEvent.TriggerPressed);
        _holdTimer.Start();
    }

    private async void OnHoldElapsed(object? sender, EventArgs args)
    {
        _holdTimer.Stop();
        if (_state != SessionState.Arming || _exiting)
        {
            return;
        }

        PreviewStreamSession? preview = null;
        try
        {
            _session = Guid.NewGuid();
            _target = ForegroundTarget.Capture();
            _bestPartial = string.Empty;
            _lastPreviewRevision = 0;

            if (_previewWorker is { IsReady: true, SupportsNativeStreaming: true } previewWorker)
            {
                try
                {
                    preview = await PreviewStreamSession.StartAsync(previewWorker, _session).ConfigureAwait(true);
                    preview.Partial += OnPreviewPartial;
                }
                catch (Exception exception)
                {
                    RuntimeLog.Write($"Preview stream start failed session={_session}: {exception}");
                    _ = RestartPreviewWorkerAsync();
                }
            }

            if (_state != SessionState.Arming || _exiting)
            {
                if (preview is not null)
                {
                    preview.Partial -= OnPreviewPartial;
                    await preview.DisposeAsync();
                }
                ResetSessionFields();
                return;
            }

            string path = Path.Combine(
                _recordingsDirectory,
                $"{DateTime.Now:yyyyMMdd-HHmmss-fff}-{_session:N}.wav");
            AudioCapture audio = new();
            if (preview is not null)
            {
                audio.Pcm16Available += OnPcm16Available;
            }
            _previewSession = preview;
            audio.Start(path);
            _audio = audio;
            Apply(SessionEvent.HoldElapsed);
            _overlay.ShowRecording(preview is not null);
        }
        catch (Exception exception)
        {
            RuntimeLog.Write($"Audio start failed session={_session}: {exception}");
            ShowError("无法开始录音", exception.Message);
            await CancelSessionAsync();
        }
    }

    private void OnPcm16Available(byte[] pcm16)
    {
        if (_previewSession?.TryEnqueue(pcm16) == false)
        {
            RuntimeLog.Write($"Preview audio ignored session={_session} bytes={pcm16.Length}");
        }
    }

    private void OnPreviewPartial(object? sender, PreviewResult result)
    {
        _ui.Post(_ =>
        {
            bool recording = _state is SessionState.Recording or SessionState.OverlayOnly;
            bool finalizing = _state == SessionState.Finalizing;
            if (!ReferenceEquals(sender, _previewSession) ||
                (!recording && !finalizing) ||
                result.Revision <= _lastPreviewRevision)
            {
                return;
            }
            _lastPreviewRevision = result.Revision;
            string text = string.IsNullOrWhiteSpace(result.Text) ? result.RawText : result.Text;
            if (string.IsNullOrWhiteSpace(text))
            {
                return;
            }
            _bestPartial = text;
            if (recording)
            {
                Apply(SessionEvent.Partial);
            }
            _overlay.ShowDraft(text, finalizing);
            RuntimeLog.Write(
                $"Preview partial session={_session} revision={result.Revision} " +
                $"textLength={text.Length} route=overlay-only finalizing={finalizing}");
        }, null);
    }

    private async Task OnTriggerReleasedAsync()
    {
        _holdTimer.Stop();
        if (_state == SessionState.Arming)
        {
            Apply(SessionEvent.TriggerReleased);
            return;
        }
        if (_state is not (SessionState.Recording or SessionState.OverlayOnly))
        {
            return;
        }
        Apply(SessionEvent.TriggerReleased);
        _overlay.ShowRecognizing(_bestPartial);
        await StopAndTranscribeAsync();
    }

    private async Task OnTriggerCancelledAsync()
    {
        _holdTimer.Stop();
        if (_state == SessionState.Arming)
        {
            Apply(SessionEvent.TriggerCancelled);
            return;
        }
        if (_state is SessionState.Recording or SessionState.OverlayOnly)
        {
            Apply(SessionEvent.TriggerCancelled);
            await CancelSessionAsync();
        }
    }

    private async Task StopAndTranscribeAsync()
    {
        CapturedAudio? captured = null;
        try
        {
            AudioCapture audio = _audio ?? throw new InvalidOperationException("录音会话已丢失");
            _audio = null;
            audio.Pcm16Available -= OnPcm16Available;
            captured = await audio.StopAsync().ConfigureAwait(true);
            await audio.DisposeAsync();
            await FinalizePreviewAsync(captured.FinalPcm16).ConfigureAwait(true);

            if (captured.Duration < TimeSpan.FromMilliseconds(500))
            {
                _overlay.ShowState("录音太短");
                await Task.Delay(800);
                FinishWithoutCommit();
                return;
            }

            AsrWorker worker = _worker ?? throw new InvalidOperationException("最终识别服务未启动");
            RuntimeLog.Write($"Transcribe start session={_session} duration={captured.Duration.TotalSeconds:F2}s path={captured.Path}");
            TranscriptionResult result = await worker.TranscribeFinalAsync(
                captured.Path,
                _session,
                RecognitionTimeout).ConfigureAwait(true);
            RuntimeLog.Write($"Transcribe complete session={_session} silence={result.Silence} textLength={result.Text.Length}");
            Apply(SessionEvent.Final);

            if (result.Silence || string.IsNullOrWhiteSpace(result.Text))
            {
                _overlay.ShowState("没有检测到清晰语音");
                await Task.Delay(1000);
                Apply(SessionEvent.CommitDone);
                _overlay.HideState();
                return;
            }

            bool inserted = CommitFinalText(result.Text, "识别结果");
            _overlay.ShowFinal(result.Text, inserted);
            await Task.Delay(1200);
            Apply(SessionEvent.CommitDone);
            _overlay.HideState();
        }
        catch (TimeoutException exception)
        {
            RuntimeLog.Write($"Transcribe timeout session={_session}: {exception.Message}");
            Apply(SessionEvent.FinalTimeout);
            await CommitBestPartialOrShowErrorAsync("最终识别超时", exception.Message);
            Apply(SessionEvent.CommitDone);
            _overlay.HideState();
            await RestartWorkerAsync();
        }
        catch (Exception exception)
        {
            RuntimeLog.Write($"Transcribe failed session={_session}: {exception}");
            Apply(SessionEvent.RecognizerFailed);
            await CommitBestPartialOrShowErrorAsync("最终识别失败", exception.Message);
            Apply(SessionEvent.CommitDone);
            _overlay.HideState();
            if (_worker is not { IsReady: true })
            {
                await RestartWorkerAsync();
            }
        }
        finally
        {
            ResetSessionFields();
        }
    }

    private bool CommitFinalText(string text, string resultName) => InsertOrCopy(text, resultName);

    private async Task FinalizePreviewAsync(byte[] tail)
    {
        PreviewStreamSession? preview = _previewSession;
        if (preview is null)
        {
            return;
        }
        try
        {
            await preview.CompleteAsync(tail).ConfigureAwait(true);
        }
        catch (Exception exception)
        {
            RuntimeLog.Write($"Preview finalize failed session={_session}: {exception}");
        }
        finally
        {
            preview.Partial -= OnPreviewPartial;
            if (ReferenceEquals(_previewSession, preview))
            {
                _previewSession = null;
            }
            await preview.DisposeAsync();
        }
    }

    private async Task CommitBestPartialOrShowErrorAsync(string title, string message)
    {
        if (string.IsNullOrWhiteSpace(_bestPartial))
        {
            ShowError(title, message);
            return;
        }
        bool inserted = CommitFinalText(_bestPartial, "实时草稿");
        _overlay.ShowState(inserted ? "实时识别结果已输入" : "最终定稿失败，实时结果已复制");
        await Task.Delay(1200);
    }

    private bool InsertOrCopy(string text, string resultName)
    {
        if (_target is not null && _target.IsStillForeground())
        {
            if (TextInjector.Insert(text))
            {
                return true;
            }
            TextInjector.Copy(text);
            ShowError("自动输入失败", $"{resultName}已复制到剪贴板，请手动粘贴");
            return false;
        }
        TextInjector.Copy(text);
        ShowError("未自动输入", $"前台窗口已变化，{resultName}已复制到剪贴板");
        return false;
    }

    private async Task CancelSessionAsync()
    {
        AudioCapture? audio = _audio;
        _audio = null;
        if (audio is not null)
        {
            audio.Pcm16Available -= OnPcm16Available;
            try
            {
                await audio.StopAsync().ConfigureAwait(true);
            }
            catch
            {
            }
            await audio.DisposeAsync();
        }
        PreviewStreamSession? preview = _previewSession;
        _previewSession = null;
        if (preview is not null)
        {
            preview.Partial -= OnPreviewPartial;
            await preview.CancelAsync().ConfigureAwait(true);
            await preview.DisposeAsync();
        }
        if (_state == SessionState.Cancelling)
        {
            Apply(SessionEvent.CancelDone);
        }
        else
        {
            _state = SessionState.Idle;
        }
        ResetSessionFields();
        _overlay.HideState();
    }

    private void FinishWithoutCommit()
    {
        if (_state == SessionState.Finalizing)
        {
            Apply(SessionEvent.RecognizerFailed);
        }
        if (_state == SessionState.Committing)
        {
            Apply(SessionEvent.CommitDone);
        }
        _overlay.HideState();
        ResetSessionFields();
    }

    private void ResetSessionFields()
    {
        _target = null;
        _session = Guid.Empty;
        _bestPartial = string.Empty;
        _lastPreviewRevision = 0;
    }

    private void Apply(SessionEvent @event)
    {
        SessionTransition transition = SessionStateMachine.Transition(_state, @event);
        RuntimeLog.Write($"Session {SessionStateMachine.WireName(_state)} --{@event}--> {SessionStateMachine.WireName(transition.State)} actions=[{string.Join(',', transition.Actions)}]");
        _state = transition.State;
    }

    private void OnWorkerFatal(object? sender, string message)
    {
        _ui.Post(async _ =>
        {
            if (_exiting)
            {
                return;
            }
            ShowError("最终识别服务错误", message);
            await RestartWorkerAsync();
        }, null);
    }

    private void OnPreviewWorkerFatal(object? sender, string message)
    {
        _ui.Post(async _ =>
        {
            if (_exiting)
            {
                return;
            }
            RuntimeLog.Write($"Preview worker fatal: {message}");
            PreviewStreamSession? preview = _previewSession;
            _previewSession = null;
            if (preview is not null)
            {
                preview.Partial -= OnPreviewPartial;
                await preview.DisposeAsync();
            }
            await RestartPreviewWorkerAsync();
        }, null);
    }

    private async Task RestartWorkerAsync()
    {
        if (_restarting || _exiting)
        {
            return;
        }
        _restarting = true;
        try
        {
            AsrWorker? old = _worker;
            _worker = null;
            if (old is not null)
            {
                old.Fatal -= OnWorkerFatal;
                await old.DisposeAsync();
            }
            _notifyIcon.Text = "麦芽 Meya 正在重启最终模型";
            _overlay.ShowState("正在重启最终识别模型…");
            await StartFinalWorkerAsync();
        }
        finally
        {
            _restarting = false;
        }
    }

    private async Task RestartPreviewWorkerAsync()
    {
        if (_previewRestarting || _exiting)
        {
            return;
        }
        _previewRestarting = true;
        try
        {
            AsrWorker? old = _previewWorker;
            _previewWorker = null;
            if (old is not null)
            {
                old.Fatal -= OnPreviewWorkerFatal;
                await old.DisposeAsync();
            }
            UpdateReadyStatus();
            await StartPreviewWorkerAsync();
        }
        finally
        {
            _previewRestarting = false;
        }
    }

    private void ShowError(string title, string message)
    {
        RuntimeLog.Write($"ERROR {title}: {message}");
        _notifyIcon.BalloonTipTitle = title;
        _notifyIcon.BalloonTipText = message;
        _notifyIcon.BalloonTipIcon = ToolTipIcon.Error;
        _notifyIcon.ShowBalloonTip(5000);
    }

    private void OpenLog()
    {
        string? path = RuntimeLog.Path;
        if (path is null)
        {
            return;
        }
        if (!File.Exists(path))
        {
            File.WriteAllText(path, string.Empty);
        }
        Process.Start(new ProcessStartInfo("notepad.exe", $"\"{path}\"") { UseShellExecute = true });
    }

    private async Task ShutdownAsync()
    {
        if (_exiting)
        {
            return;
        }
        _exiting = true;
        _holdTimer.Stop();
        _trigger.Dispose();
        await CancelSessionAsync();

        AsrWorker? previewWorker = _previewWorker;
        _previewWorker = null;
        if (previewWorker is not null)
        {
            previewWorker.Fatal -= OnPreviewWorkerFatal;
            await previewWorker.DisposeAsync();
        }
        AsrWorker? worker = _worker;
        _worker = null;
        if (worker is not null)
        {
            worker.Fatal -= OnWorkerFatal;
            await worker.DisposeAsync();
        }
        _notifyIcon.Visible = false;
        _notifyIcon.Dispose();
        _overlay.Dispose();
        _holdTimer.Dispose();
        ExitThread();
    }
}
