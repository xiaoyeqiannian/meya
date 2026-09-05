using System.Collections.ObjectModel;
using Avalonia.Controls;
using Avalonia.Interactivity;
using Meya.Core;

namespace Meya.UI.Views;

public partial class TrainingDataWindow : Window
{
    private TrainingDataStore? _store;
    private Action? _openFolder;

    public TrainingDataWindow()
    {
        InitializeComponent();
        DataContext = this;
    }

    public ObservableCollection<TrainingSampleItem> Samples { get; } = [];
    public TrainingSampleItem? SelectedSample { get; set; }

    public void Configure(TrainingDataStore store, Action openFolder)
    {
        _store = store;
        _openFolder = openFolder;
        Samples.Clear();
        foreach (TrainingSampleItem sample in store.Load()) Samples.Add(sample);
        StatusText.Text = Samples.Count == 0 ? "还没有训练样本。" : $"共 {Samples.Count} 条本地样本";
    }

    private void OpenFolder(object? sender, RoutedEventArgs args) => _openFolder?.Invoke();

    private void DeleteSelected(object? sender, RoutedEventArgs args)
    {
        if (_store is null || SelectedSample is null)
        {
            StatusText.Text = "请先选择一个样本";
            return;
        }
        _store.Delete(SelectedSample.SampleId);
        Samples.Remove(SelectedSample);
        StatusText.Text = "已删除所选样本及其本地音频";
    }
}
