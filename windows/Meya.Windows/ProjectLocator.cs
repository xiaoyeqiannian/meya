namespace Meya.Windows;

internal static class ProjectLocator
{
    internal static string Locate()
    {
        string baseDirectory = AppContext.BaseDirectory;
        string pointer = Path.Combine(baseDirectory, "project-root.txt");
        if (File.Exists(pointer))
        {
            string configured = File.ReadAllText(pointer).Trim();
            if (IsRoot(configured))
            {
                return Path.GetFullPath(configured);
            }
        }

        DirectoryInfo? current = new(baseDirectory);
        while (current is not null)
        {
            if (IsRoot(current.FullName))
            {
                return current.FullName;
            }
            current = current.Parent;
        }
        throw new DirectoryNotFoundException("找不到麦芽运行目录（缺少 asr_daemon.py）");
    }

    private static bool IsRoot(string path) =>
        !string.IsNullOrWhiteSpace(path) &&
        File.Exists(Path.Combine(path, "asr_daemon.py")) &&
        File.Exists(Path.Combine(path, "model-config.json"));
}
