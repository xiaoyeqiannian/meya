using Avalonia.Controls;
using Avalonia.Interactivity;

namespace Meya.UI.Views;

public partial class LearnCorrectionWindow : Window
{
    private Func<string, string, Task>? _submit;

    public LearnCorrectionWindow() => InitializeComponent();

    public void Configure(string original, Func<string, string, Task> submit)
    {
        OriginalText.Text = original;
        CorrectedText.Text = original;
        _submit = submit;
    }

    private async void Submit(object? sender, RoutedEventArgs args)
    {
        string original = OriginalText.Text?.Trim() ?? string.Empty;
        string corrected = CorrectedText.Text?.Trim() ?? string.Empty;
        if (original.Length == 0 || corrected.Length == 0 || original == corrected)
        {
            StatusText.Text = "请在正确文本中修改识别错误的部分";
            return;
        }
        if (original.Length > 8000 || corrected.Length > 8000)
        {
            StatusText.Text = "文本过长，无法学习";
            return;
        }
        try
        {
            StatusText.Text = "正在本地学习…";
            await _submit!(original, corrected);
            Close();
        }
        catch (Exception exception)
        {
            StatusText.Text = $"学习失败：{exception.Message}";
        }
    }

    private void Cancel(object? sender, RoutedEventArgs args) => Close();
}
