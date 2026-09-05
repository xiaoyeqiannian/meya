using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Markup.Xaml;

namespace Meya.Windows;

public partial class App : Application
{
    private TrayApplicationContext? _host;

    public override void Initialize() => AvaloniaXamlLoader.Load(this);

    public override void OnFrameworkInitializationCompleted()
    {
        if (ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
        {
            desktop.ShutdownMode = ShutdownMode.OnExplicitShutdown;
            _host = new TrayApplicationContext(desktop);
            desktop.Exit += async (_, _) =>
            {
                if (_host is not null)
                {
                    await _host.ShutdownAsync(shutdownApplication: false);
                    _host = null;
                }
            };
        }
        base.OnFrameworkInitializationCompleted();
    }
}
