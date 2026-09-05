using System.Diagnostics;
using System.Reflection;
using System.Text.Json;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Platform;
using Avalonia.Threading;

namespace Meya.Windows;

internal sealed class TrayApplicationContext
{
    private static readonly TimeSpan HoldThreshold = TimeSpan.FromMilliseconds(350);
    private static readonly TimeSpan StartupTimeout = TimeSpan.FromMinutes(3);
    private static readonly TimeSpan RecognitionTimeout = TimeSpan.FromSeconds(60);

    private readonly SynchronizationContext _ui;
    private readonly IClassicDesktopStyleApplicationLifetime _desktop;
    private readonly string _projectRoot;
    private readonly string _userDataDirectory;
    private readonly string _runtimeDirectory;
    private readonly string _recordingsDirectory;
    private ModelSelection _models;
    private readonly TrayIcon _notifyIcon;
    private readonly NativeMenuItem _statusItem;
    private readonly NativeMenuItem _learnItem;
    private readonly OverlayForm _overlay = new();
    private readonly GlobalRightControl _trigger;
    private readonly DispatcherTimer _holdTimer;

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
    private KeywordLibraryWindow? _keywordLibraryWindow;
    private ModelManagerWindow? _modelManagerWindow;
    private LearnCorrectionWindow? _learnCorrectionWindow;
    private LearningRulesWindow? _learningRulesWindow;
    private TrainingDataWindow? _trainingDataWindow;
    private DiagnosticsWindow? _diagnosticsWindow;
    private LastRecognition? _lastRecognition;

    internal TrayApplicationContext(IClassicDesktopStyleApplicationLifetime desktop)
    {
        _desktop = desktop;
        _ui = SynchronizationContext.Current
            ?? throw new InvalidOperationException("Avalonia UI synchronization context is unavailable");
        _projectRoot = ProjectLocator.Locate();
        _userDataDirectory = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Meya");
        _runtimeDirectory = Path.Combine(_userDataDirectory, "runtime");
        _recordingsDirectory = Path.Combine(_userDataDirectory, "recordings", "voice-input");
        Directory.CreateDirectory(_recordingsDirectory);
        RuntimeLog.Configure(_runtimeDirectory);
        _models = ModelSelection.Load(_projectRoot, _userDataDirectory);

        string version = Assembly.GetExecutingAssembly().GetName().Version?.ToString(3) ?? "0.1.0";
        IReadOnlyList<MenuEntry> model = MeyaMenu.Create(
            version,
            "○ 麦芽正在加载识别模型…",
            canLearnLastCorrection: false);
        Dictionary<string, NativeMenuItem> items = [];
        NativeMenu menu = BuildMenu(model, items);
        _statusItem = items["status"];
        _learnItem = items["learn-last-correction"];

        using Stream iconStream = AssetLoader.Open(new Uri("avares://Meya.Windows/Assets/MeyaLogo.png"));
        _notifyIcon = new TrayIcon
        {
            Icon = new WindowIcon(iconStream),
            ToolTipText = "麦芽 Meya 正在加载识别模型",
            Menu = menu,
            IsVisible = true,
        };
        _notifyIcon.Clicked += (_, _) => _overlay.ShowState(StatusText());
        TrayIcon.SetIcons(Application.Current!, new TrayIcons { _notifyIcon });

        _holdTimer = new DispatcherTimer { Interval = HoldThreshold };
        _holdTimer.Tick += OnHoldElapsed;

        _trigger = new GlobalRightControl();
        _trigger.Pressed += (_, _) => _ui.Post(_ => OnTriggerPressed(), null);
        _trigger.Released += (_, _) => _ui.Post(async _ => await OnTriggerReleasedAsync(), null);
        _trigger.Cancelled += (_, _) => _ui.Post(async _ => await OnTriggerCancelledAsync(), null);

        RuntimeLog.Write($"Windows Avalonia host started root={_projectRoot}");
        _ = StartWorkersAsync();
    }

    private NativeMenu BuildMenu(IEnumerable<MenuEntry> entries, IDictionary<string, NativeMenuItem> items)
    {
        NativeMenu menu = new();
        foreach (MenuEntry entry in entries)
        {
            if (entry.Kind == MenuEntryKind.Separator)
            {
                menu.Items.Add(new NativeMenuItemSeparator());
                continue;
            }

            NativeMenuItem item = new(entry.Label) { IsEnabled = entry.Enabled };
            items[entry.Key] = item;
            if (entry.Children is { Count: > 0 })
            {
                item.Menu = BuildMenu(entry.Children, items);
            }
            else if (entry.Kind == MenuEntryKind.Command)
            {
                item.Click += async (_, _) => await ExecuteMenuCommandAsync(entry.Key);
            }
            menu.Items.Add(item);
        }
        return menu;
    }

    private async Task ExecuteMenuCommandAsync(string key)
    {
        switch (key)
        {
            case "learn-last-correction":
                await LearnLastCorrectionAsync();
                break;
            case "personal-glossary":
                ShowKeywordLibrary();
                break;
            case "model-manager":
                ShowModelManager();
                break;
            case "open-recordings":
                OpenDirectory(_recordingsDirectory);
                break;
            case "permissions-diagnostics":
                ShowDiagnostics();
                break;
            case "exit":
                await ShutdownAsync();
                break;
        }
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
        string text = StatusText();
        _statusItem.Header = text;
        _notifyIcon.ToolTipText = text;
    }

    private string StatusText()
    {
        if (_worker is not { IsReady: true })
        {
            return "○ 麦芽正在加载识别模型…";
        }
        return _previewWorker is { IsReady: true, SupportsNativeStreaming: true }
            ? "● 麦芽已就绪 · 长按右 Ctrl 输入"
            : "◐ 最终定稿已就绪 · 实时草稿不可用";
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
            audio.LevelAvailable += OnAudioLevelAvailable;
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

    private void OnAudioLevelAvailable(float level) =>
        _overlay.UpdateAudioLevel(level);

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
            audio.LevelAvailable -= OnAudioLevelAvailable;
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

            _lastRecognition = new LastRecognition(
                result.Text,
                result.RawText,
                captured.Path,
                result.Model);
            _learnItem.Header = "学习刚才的修改";
            _learnItem.IsEnabled = true;

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
            audio.LevelAvailable -= OnAudioLevelAvailable;
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
            _statusItem.Header = "○ 正在重启最终识别模型…";
            _notifyIcon.ToolTipText = "麦芽 Meya 正在重启最终模型";
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
        _overlay.ShowState($"{title} · {message}");
    }

    private Task LearnLastCorrectionAsync()
    {
        if (_lastRecognition is null)
        {
            _overlay.ShowState("暂无可学习的修改 · 先完成一次语音输入");
            return Task.CompletedTask;
        }
        if (_learnCorrectionWindow is null)
        {
            LearnCorrectionWindow window = new();
            window.Configure(_lastRecognition.FinalText, SubmitFeedbackAsync);
            window.Closed += (_, _) => _learnCorrectionWindow = null;
            _learnCorrectionWindow = window;
            window.Show();
        }
        else
        {
            _learnCorrectionWindow.Activate();
        }
        return Task.CompletedTask;
    }

    private async Task SubmitFeedbackAsync(string original, string corrected)
    {
        LastRecognition recognition = _lastRecognition
            ?? throw new InvalidOperationException("上一条识别记录已经失效");
        AsrWorker worker = _worker ?? throw new InvalidOperationException("学习服务尚未就绪");
        JsonElement response = await worker.SendRequestAsync(
            new Dictionary<string, object?>
            {
                ["command"] = "feedback",
                ["expected_text"] = original,
                ["edited_text"] = corrected,
                ["raw_text"] = recognition.RawText,
                ["final_text"] = recognition.FinalText,
                ["audio_path"] = recognition.AudioPath,
                ["app_name"] = "Windows",
                ["explicit"] = true,
                ["recognition_model"] = recognition.Model,
            },
            Guid.NewGuid(),
            TimeSpan.FromSeconds(10));
        ThrowIfResponseError(response);
        string mapping = FirstLearnedMapping(response) ?? "已保存本地学习记录";
        _overlay.ShowState(mapping);
        _lastRecognition = null;
        _learnItem.Header = "暂无可学习的修改";
        _learnItem.IsEnabled = false;
        await RestartWorkerAsync();
    }

    private void ShowKeywordLibrary()
    {
        if (_keywordLibraryWindow is null)
        {
            KeywordLibraryWindow window = new();
            window.Configure(
                new GlossaryStore(_userDataDirectory, _projectRoot),
                RestartWorkerAsync,
                ShowLearningRules,
                ShowTrainingData);
            window.Closed += (_, _) => _keywordLibraryWindow = null;
            _keywordLibraryWindow = window;
            window.Show();
        }
        else
        {
            _keywordLibraryWindow.Activate();
        }
    }

    private void ShowLearningRules() => _ = ShowLearningRulesAsync();

    private async Task ShowLearningRulesAsync()
    {
        try
        {
            AsrWorker worker = _worker ?? throw new InvalidOperationException("学习服务尚未就绪");
            JsonElement response = await worker.SendRequestAsync(
                new Dictionary<string, object?> { ["command"] = "list_learning_rules" },
                Guid.NewGuid(),
                TimeSpan.FromSeconds(10));
            ThrowIfResponseError(response);
            List<LearningRuleItem> rules = [];
            if (response.TryGetProperty("rules", out JsonElement values) && values.ValueKind == JsonValueKind.Array)
            {
                foreach (JsonElement value in values.EnumerateArray())
                {
                    rules.Add(new LearningRuleItem
                    {
                        Id = JsonInt(value, "id"),
                        Observed = JsonString(value, "observed"),
                        Canonical = JsonString(value, "canonical"),
                        Confirmations = JsonInt(value, "confirmations"),
                        HitCount = JsonInt(value, "hit_count"),
                        Activated = JsonBool(value, "activated"),
                        Evidence = JsonString(value, "evidence"),
                        UpdatedAt = JsonString(value, "updated_at"),
                    });
                }
            }
            _learningRulesWindow?.Close();
            LearningRulesWindow window = new();
            window.Configure(rules, RollbackLearningRuleAsync);
            window.Closed += (_, _) => _learningRulesWindow = null;
            _learningRulesWindow = window;
            window.Show();
        }
        catch (Exception exception)
        {
            ShowError("读取已学规则失败", exception.Message);
        }
    }

    private async Task RollbackLearningRuleAsync(int ruleId)
    {
        AsrWorker worker = _worker ?? throw new InvalidOperationException("学习服务尚未就绪");
        JsonElement response = await worker.SendRequestAsync(
            new Dictionary<string, object?>
            {
                ["command"] = "rollback_learning_rule",
                ["rule_id"] = ruleId,
            },
            Guid.NewGuid(),
            TimeSpan.FromSeconds(10));
        ThrowIfResponseError(response);
        await RestartWorkerAsync();
    }

    private void ShowTrainingData()
    {
        if (_trainingDataWindow is null)
        {
            string path = Path.Combine(_userDataDirectory, "training-data");
            TrainingDataWindow window = new();
            window.Configure(new TrainingDataStore(_userDataDirectory), () => OpenDirectory(path));
            window.Closed += (_, _) => _trainingDataWindow = null;
            _trainingDataWindow = window;
            window.Show();
        }
        else
        {
            _trainingDataWindow.Activate();
        }
    }

    private void ShowModelManager()
    {
        if (_modelManagerWindow is null)
        {
            ModelManagerWindow window = new();
            window.Configure(
                DiscoverModels(),
                _models.Preview,
                _models.Final,
                ModelSelection.DefaultPreview,
                ModelSelection.DefaultFinal,
                ApplyModelsAsync);
            window.Closed += (_, _) => _modelManagerWindow = null;
            _modelManagerWindow = window;
            window.Show();
        }
        else
        {
            _modelManagerWindow.Activate();
        }
    }

    private IEnumerable<string> DiscoverModels()
    {
        HashSet<string> models = new(StringComparer.OrdinalIgnoreCase)
        {
            ModelSelection.DefaultPreview,
            ModelSelection.DefaultFinal,
            _models.Preview,
            _models.Final,
        };
        string root = Path.Combine(_projectRoot, "models");
        if (Directory.Exists(root))
        {
            foreach (string modelFile in Directory.EnumerateFiles(root, "model.pt", SearchOption.AllDirectories))
            {
                string? directory = Path.GetDirectoryName(modelFile);
                if (directory is not null && File.Exists(Path.Combine(directory, "config.yaml")))
                {
                    models.Add("paraformer:" + directory);
                }
            }
        }
        return models.Order(StringComparer.OrdinalIgnoreCase).ToArray();
    }

    private async Task ApplyModelsAsync(string preview, string final)
    {
        _models = new ModelSelection(preview.Trim(), final.Trim());
        _models.Save(_userDataDirectory);
        await Task.WhenAll(RestartWorkerAsync(), RestartPreviewWorkerAsync());
    }

    private void ShowDiagnostics()
    {
        string report = string.Join(Environment.NewLine,
        [
            $"版本：{Assembly.GetExecutingAssembly().GetName().Version}",
            $"右 Ctrl 全局监听：{(_exiting ? "已停止" : "已安装")}",
            $"实时识别 worker：{(_previewWorker is { IsReady: true } ? "已就绪" : "不可用")}",
            $"最终识别 worker：{(_worker is { IsReady: true } ? "已就绪" : "不可用")}",
            $"实时流式识别：{(_previewWorker is { SupportsNativeStreaming: true } ? "支持" : "不可用")}",
            $"用户数据：{_userDataDirectory}",
            $"录音目录：{_recordingsDirectory}",
            $"诊断日志：{RuntimeLog.Path ?? "尚未创建"}",
        ]);
        if (_diagnosticsWindow is null)
        {
            DiagnosticsWindow window = new();
            window.Configure(
                report,
                () => Process.Start(new ProcessStartInfo("ms-settings:privacy-microphone") { UseShellExecute = true }),
                OpenLog,
                () => OpenDirectory(_recordingsDirectory));
            window.Closed += (_, _) => _diagnosticsWindow = null;
            _diagnosticsWindow = window;
            window.Show();
        }
        else
        {
            _diagnosticsWindow.Configure(
                report,
                () => Process.Start(new ProcessStartInfo("ms-settings:privacy-microphone") { UseShellExecute = true }),
                OpenLog,
                () => OpenDirectory(_recordingsDirectory));
            _diagnosticsWindow.Activate();
        }
    }

    private static void ThrowIfResponseError(JsonElement response)
    {
        if (response.TryGetProperty("error", out JsonElement error) && error.ValueKind == JsonValueKind.String)
        {
            throw new InvalidOperationException(error.GetString());
        }
    }

    private static string? FirstLearnedMapping(JsonElement response)
    {
        foreach (string property in new[] { "activated", "observed" })
        {
            if (!response.TryGetProperty(property, out JsonElement values) || values.ValueKind != JsonValueKind.Array)
            {
                continue;
            }
            foreach (JsonElement value in values.EnumerateArray())
            {
                string observed = JsonString(value, "observed");
                string canonical = JsonString(value, "canonical");
                if (observed.Length > 0 && canonical.Length > 0)
                {
                    return $"已学习：{observed} → {canonical}";
                }
            }
        }
        return null;
    }

    private static string JsonString(JsonElement value, string name) =>
        value.TryGetProperty(name, out JsonElement property) && property.ValueKind == JsonValueKind.String
            ? property.GetString() ?? string.Empty
            : string.Empty;

    private static int JsonInt(JsonElement value, string name) =>
        value.TryGetProperty(name, out JsonElement property) && property.TryGetInt32(out int result) ? result : 0;

    private static bool JsonBool(JsonElement value, string name) =>
        value.TryGetProperty(name, out JsonElement property) && property.ValueKind == JsonValueKind.True;

    private static void OpenDirectory(string path)
    {
        Directory.CreateDirectory(path);
        Process.Start(new ProcessStartInfo("explorer.exe", $"\"{path}\"") { UseShellExecute = true });
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

    private sealed record LastRecognition(
        string FinalText,
        string RawText,
        string AudioPath,
        string Model);

    internal async Task ShutdownAsync(bool shutdownApplication = true)
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
        _notifyIcon.IsVisible = false;
        _notifyIcon.Dispose();
        _overlay.Dispose();
        if (shutdownApplication)
        {
            _desktop.Shutdown();
        }
    }
}
