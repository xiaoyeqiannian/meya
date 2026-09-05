using System.Collections.ObjectModel;
using Avalonia.Controls;
using Avalonia.Interactivity;
using Meya.Core;

namespace Meya.UI.Views;

public partial class LearningRulesWindow : Window
{
    private Func<int, Task>? _rollback;

    public LearningRulesWindow()
    {
        InitializeComponent();
        DataContext = this;
    }

    public ObservableCollection<LearningRuleItem> Rules { get; } = [];
    public LearningRuleItem? SelectedRule { get; set; }

    public void Configure(IEnumerable<LearningRuleItem> rules, Func<int, Task> rollback)
    {
        Rules.Clear();
        foreach (LearningRuleItem rule in rules) Rules.Add(rule);
        _rollback = rollback;
        StatusText.Text = Rules.Count == 0 ? "还没有从修改中提取出学习规则。" : $"共 {Rules.Count} 条规则";
    }

    private async void Rollback(object? sender, RoutedEventArgs args)
    {
        if (SelectedRule is null || _rollback is null)
        {
            StatusText.Text = "请先选择一条规则";
            return;
        }
        try
        {
            await _rollback(SelectedRule.Id);
            Rules.Remove(SelectedRule);
            StatusText.Text = "已撤销学习规则";
        }
        catch (Exception exception)
        {
            StatusText.Text = $"撤销失败：{exception.Message}";
        }
    }
}
