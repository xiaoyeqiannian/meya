using Avalonia.Controls;
using Avalonia.Interactivity;

namespace Meya.UI.Views;

public partial class DiagnosticsWindow : Window
{
    private Action? _openPrivacy;
    private Action? _openLog;
    private Action? _openRecordings;

    public DiagnosticsWindow() => InitializeComponent();

    public void Configure(string report, Action openPrivacy, Action openLog, Action openRecordings)
    {
        ReportText.Text = report;
        _openPrivacy = openPrivacy;
        _openLog = openLog;
        _openRecordings = openRecordings;
    }

    private void OpenPrivacy(object? sender, RoutedEventArgs args) => _openPrivacy?.Invoke();
    private void OpenLog(object? sender, RoutedEventArgs args) => _openLog?.Invoke();
    private void OpenRecordings(object? sender, RoutedEventArgs args) => _openRecordings?.Invoke();
}
