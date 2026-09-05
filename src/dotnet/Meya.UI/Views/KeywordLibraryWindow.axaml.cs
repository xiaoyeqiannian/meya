using System.Collections.ObjectModel;
using Avalonia.Controls;
using Avalonia.Interactivity;
using Avalonia.Platform.Storage;
using Meya.Core;

namespace Meya.UI.Views;

public partial class KeywordLibraryWindow : Window
{
    private GlossaryStore? _store;
    private Func<Task>? _onSaved;
    private Action? _openLearningRules;
    private Action? _openTrainingData;

    public KeywordLibraryWindow()
    {
        InitializeComponent();
        DataContext = this;
    }

    public ObservableCollection<GlossaryEntry> Entries { get; private set; } = [];
    public GlossaryEntry? SelectedEntry { get; set; }

    public void Configure(
        GlossaryStore store,
        Func<Task> onSaved,
        Action openLearningRules,
        Action openTrainingData)
    {
        _store = store;
        _onSaved = onSaved;
        _openLearningRules = openLearningRules;
        _openTrainingData = openTrainingData;
        Entries = store.Load();
        DataContext = null;
        DataContext = this;
    }

    private void AddTerm(object? sender, RoutedEventArgs args)
    {
        Entries.Add(new GlossaryEntry());
        StatusText.Text = "已添加空白术语，请填写标准写法";
    }

    private void RemoveSelected(object? sender, RoutedEventArgs args)
    {
        if (SelectedEntry is not null)
        {
            Entries.Remove(SelectedEntry);
        }
    }

    private async void ImportTerms(object? sender, RoutedEventArgs args)
    {
        if (_store is null)
        {
            return;
        }
        IReadOnlyList<IStorageFile> files = await StorageProvider.OpenFilePickerAsync(new FilePickerOpenOptions
        {
            Title = "导入个人术语",
            AllowMultiple = false,
            FileTypeFilter =
            [
                new FilePickerFileType("术语文件") { Patterns = ["*.txt", "*.csv", "*.tsv"] },
            ],
        });
        if (files.Count == 0 || files[0].TryGetLocalPath() is not { } path)
        {
            return;
        }
        IReadOnlyList<GlossaryEntry> imported = _store.Import(path);
        foreach (GlossaryEntry entry in imported)
        {
            GlossaryEntry? existing = Entries.FirstOrDefault(item =>
                string.Equals(item.Canonical, entry.Canonical, StringComparison.OrdinalIgnoreCase));
            if (existing is null)
            {
                Entries.Add(entry);
            }
            else
            {
                if (!string.IsNullOrWhiteSpace(entry.Pronunciations)) existing.Pronunciations = entry.Pronunciations;
                if (!string.IsNullOrWhiteSpace(entry.Corrections)) existing.Corrections = entry.Corrections;
            }
        }
        StatusText.Text = $"已导入 {imported.Count} 条术语";
    }

    private void AcceptSuggestion(object? sender, RoutedEventArgs args)
    {
        if (SelectedEntry is null || string.IsNullOrWhiteSpace(SelectedEntry.Suggestion))
        {
            StatusText.Text = "所选术语没有可采纳的发音建议";
            return;
        }
        int index = Entries.IndexOf(SelectedEntry);
        string suggestion = SelectedEntry.Suggestion.Split('、', StringSplitOptions.RemoveEmptyEntries)[0].Trim();
        string pronunciations = string.IsNullOrWhiteSpace(SelectedEntry.Pronunciations)
            ? suggestion
            : SelectedEntry.Pronunciations + "、" + suggestion;
        GlossaryEntry updated = new()
        {
            Canonical = SelectedEntry.Canonical,
            Pronunciations = pronunciations,
            Corrections = SelectedEntry.Corrections,
            Suggestion = string.Empty,
            HotwordStatus = "— 待检测",
        };
        Entries[index] = updated;
        SelectedEntry = updated;
        StatusText.Text = $"已采纳发音：{suggestion}";
    }

    private async void SaveTerms(object? sender, RoutedEventArgs args)
    {
        if (_store is null || _onSaved is null)
        {
            return;
        }
        try
        {
            _store.Save(Entries);
            StatusText.Text = "正在刷新最终识别模型…";
            await _onSaved();
            StatusText.Text = "个人词库已保存并立即生效";
        }
        catch (Exception exception)
        {
            StatusText.Text = $"保存失败：{exception.Message}";
        }
    }

    private void OpenLearningRules(object? sender, RoutedEventArgs args) => _openLearningRules?.Invoke();
    private void OpenTrainingData(object? sender, RoutedEventArgs args) => _openTrainingData?.Invoke();
}
