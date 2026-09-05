using System.Reflection;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Platform;
using Avalonia.Threading;
using Meya.Core;
using Meya.UI.Views;

namespace Meya.Desktop;

public partial class App : Application
{
    private TrayIcon? _trayIcon;
    private OverlayWindow? _overlay;
    private bool _showingDraft;

    public override void Initialize() => Avalonia.Markup.Xaml.AvaloniaXamlLoader.Load(this);

    public override void OnFrameworkInitializationCompleted()
    {
        if (ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
        {
            desktop.ShutdownMode = ShutdownMode.OnExplicitShutdown;
            _overlay = new OverlayWindow();
            if (OperatingSystem.IsWindows())
            {
                _overlay.NativeWindowReady += WindowsOverlayBehavior.Configure;
            }

            string version = Assembly.GetExecutingAssembly().GetName().Version?.ToString(3) ?? "0.1.0";
            IReadOnlyList<MenuEntry> model = MeyaMenu.Create(
                version,
                "○ 麦芽正在加载识别模型…",
                canLearnLastCorrection: false);
            NativeMenu menu = BuildMenu(model, desktop);
            using Stream iconStream = AssetLoader.Open(new Uri("avares://Meya.Desktop/Assets/MeyaLogo.png"));
            _trayIcon = new TrayIcon
            {
                Icon = new WindowIcon(iconStream),
                ToolTipText = "麦芽 Meya · 跨平台界面验证",
                Menu = menu,
                IsVisible = true,
            };
            _trayIcon.Clicked += (_, _) => ToggleOverlay();
            TrayIcon.SetIcons(this, new TrayIcons { _trayIcon });

            bool overlaySmoke = Environment.GetCommandLineArgs().Contains("--overlay-smoke", StringComparer.OrdinalIgnoreCase);
            bool overlayPreview = Environment.GetCommandLineArgs().Contains("--overlay-preview", StringComparer.OrdinalIgnoreCase);
            if (overlaySmoke || overlayPreview)
            {
                _overlay.ShowPresentation(OverlayPresentation.Draft(
                    "Avalonia 共享浮层冒烟测试：大兔子、动态文本、无激活和鼠标穿透。",
                    OperatingSystem.IsMacOS() ? "Fn" : "右 Ctrl"));
            }
            if (overlaySmoke)
            {
                DispatcherTimer.RunOnce(() =>
                {
                    _trayIcon.IsVisible = false;
                    _overlay.Close();
                    desktop.Shutdown();
                }, TimeSpan.FromSeconds(2));
            }
        }
        base.OnFrameworkInitializationCompleted();
    }

    private NativeMenu BuildMenu(IEnumerable<MenuEntry> entries, IClassicDesktopStyleApplicationLifetime desktop)
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
            if (entry.Children is { Count: > 0 })
            {
                item.Menu = BuildMenu(entry.Children, desktop);
            }
            else if (entry.Enabled)
            {
                item.Click += (_, _) => Execute(entry.Key, desktop);
            }
            menu.Items.Add(item);
        }
        return menu;
    }

    private void Execute(string key, IClassicDesktopStyleApplicationLifetime desktop)
    {
        switch (key)
        {
            case "exit":
                _trayIcon!.IsVisible = false;
                _overlay?.Close();
                desktop.Shutdown();
                break;
            case "personal-glossary":
            case "model-manager":
            case "open-recordings":
            case "permissions-diagnostics":
                _overlay?.ShowPresentation(OverlayPresentation.Message("共享管理窗口将在下一迁移阶段接入"));
                break;
        }
    }

    private void ToggleOverlay()
    {
        if (_overlay is null)
        {
            return;
        }
        _showingDraft = !_showingDraft;
        _overlay.ShowPresentation(_showingDraft
            ? OverlayPresentation.Draft("Avalonia 共享浮层已运行，macOS 与 Windows 使用同一套布局和状态模型。", "右 Ctrl")
            : OverlayPresentation.Hidden());
    }
}
