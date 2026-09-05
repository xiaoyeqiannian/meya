namespace Meya.Core;

public enum MenuEntryKind
{
    Command,
    Status,
    Separator,
    Submenu,
}

public sealed record MenuEntry(
    string Key,
    string Label,
    MenuEntryKind Kind = MenuEntryKind.Command,
    bool Enabled = true,
    IReadOnlyList<MenuEntry>? Children = null);

public static class MeyaMenu
{
    public static IReadOnlyList<MenuEntry> Create(
        string version,
        string status,
        bool canLearnLastCorrection)
    {
        string normalizedVersion = string.IsNullOrWhiteSpace(version) ? "0.0.0" : version.Trim();
        return
        [
            new("version", $"麦芽 Meya · v{normalizedVersion}", MenuEntryKind.Status, false),
            new("status", status, MenuEntryKind.Status, false),
            Separator("separator-status"),
            new(
                "learn-last-correction",
                canLearnLastCorrection ? "学习刚才的修改" : "暂无可学习的修改",
                MenuEntryKind.Command,
                canLearnLastCorrection),
            new("personal-glossary", "管理个人词库…"),
            new("model-manager", "管理识别模型…"),
            Separator("separator-management"),
            new(
                "more",
                "更多",
                MenuEntryKind.Submenu,
                true,
                [
                    new("open-recordings", "打开录音目录…"),
                    Separator("separator-more"),
                    new("permissions-diagnostics", "权限与诊断…"),
                ]),
            Separator("separator-exit"),
            new("exit", "退出麦芽 Meya"),
        ];
    }

    private static MenuEntry Separator(string key) => new(key, string.Empty, MenuEntryKind.Separator, false);
}
