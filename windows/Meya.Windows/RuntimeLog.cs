using System.Text;

namespace Meya.Windows;

internal static class RuntimeLog
{
    private static readonly object Sync = new();
    private static string? _path;

    internal static void Configure(string runtimeDirectory)
    {
        Directory.CreateDirectory(runtimeDirectory);
        _path = System.IO.Path.Combine(runtimeDirectory, "windows.log");
    }

    internal static void Write(string message)
    {
        try
        {
            string? path = _path;
            if (path is null)
            {
                return;
            }
            string line = $"{DateTimeOffset.Now:O} {message}{Environment.NewLine}";
            lock (Sync)
            {
                File.AppendAllText(path, line, new UTF8Encoding(false));
            }
        }
        catch
        {
            // Diagnostics must never interrupt voice input.
        }
    }

    internal static string? Path => _path;
}
