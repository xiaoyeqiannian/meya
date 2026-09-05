using System.Collections.ObjectModel;
using Avalonia.Controls;
using Avalonia.Interactivity;
using Avalonia.Platform.Storage;

namespace Meya.UI.Views;

public partial class ModelManagerWindow : Window
{
    private Func<string, string, Task>? _apply;
    private string _defaultPreview = string.Empty;
    private string _defaultFinal = string.Empty;

    public ModelManagerWindow()
    {
        InitializeComponent();
        DataContext = this;
    }

    public ObservableCollection<string> Models { get; } = [];
    public string? PreviewModel { get; set; }
    public string? FinalModel { get; set; }

    public void Configure(
        IEnumerable<string> models,
        string preview,
        string final,
        string defaultPreview,
        string defaultFinal,
        Func<string, string, Task> apply)
    {
        Models.Clear();
        foreach (string model in models.Append(preview).Append(final).Distinct(StringComparer.OrdinalIgnoreCase))
        {
            Models.Add(model);
        }
        PreviewModel = preview;
        FinalModel = final;
        _defaultPreview = defaultPreview;
        _defaultFinal = defaultFinal;
        _apply = apply;
        RunningRoles.Text = $"当前运行：实时 · {preview}\n最终 · {final}";
        DataContext = null;
        DataContext = this;
    }

    private async void ChooseLocalModel(object? sender, RoutedEventArgs args)
    {
        IReadOnlyList<IStorageFolder> folders = await StorageProvider.OpenFolderPickerAsync(new FolderPickerOpenOptions
        {
            Title = "选择本地识别模型目录",
            AllowMultiple = false,
        });
        if (folders.Count == 0 || folders[0].TryGetLocalPath() is not { } path)
        {
            return;
        }
        string identifier = "paraformer:" + path;
        if (!Models.Contains(identifier))
        {
            Models.Add(identifier);
        }
        StatusText.Text = "已添加本地模型，请选择它用于实时或最终识别";
    }

    private void RestoreDefaults(object? sender, RoutedEventArgs args)
    {
        PreviewModel = _defaultPreview;
        FinalModel = _defaultFinal;
        DataContext = null;
        DataContext = this;
        StatusText.Text = "已恢复推荐配置，点击“应用两个模型”生效";
    }

    private async void ApplyModels(object? sender, RoutedEventArgs args)
    {
        if (_apply is null || string.IsNullOrWhiteSpace(PreviewModel) || string.IsNullOrWhiteSpace(FinalModel))
        {
            StatusText.Text = "请选择实时和最终识别模型";
            return;
        }
        try
        {
            StatusText.Text = "正在重启两个识别模型…";
            await _apply(PreviewModel, FinalModel);
            RunningRoles.Text = $"当前运行：实时 · {PreviewModel}\n最终 · {FinalModel}";
            StatusText.Text = "两个模型已应用";
        }
        catch (Exception exception)
        {
            StatusText.Text = $"应用失败：{exception.Message}";
        }
    }
}
