namespace Meya.Core;

public enum OverlayPhase
{
    Hidden,
    Status,
    Recording,
    Draft,
    Finalizing,
    Final,
}

public sealed record OverlayPresentation(
    OverlayPhase Phase,
    string Status,
    string Text,
    bool Success = false)
{
    public const int MaximumVisibleCharacters = 24;

    public static OverlayPresentation Hidden() => new(OverlayPhase.Hidden, string.Empty, string.Empty);

    public static OverlayPresentation Message(string text) =>
        new(OverlayPhase.Status, string.Empty, Latest(text));

    public static OverlayPresentation Recording(string triggerName, bool streamingAvailable) =>
        new(
            OverlayPhase.Recording,
            string.Empty,
            streamingAvailable
                ? $"正在听 · 松开{triggerName} 完成"
                : $"正在录音 · 松开{triggerName} 完成");

    public static OverlayPresentation Draft(string text, string triggerName, bool finalizing = false) =>
        new(
            finalizing ? OverlayPhase.Finalizing : OverlayPhase.Draft,
            finalizing ? "正在使用 SeACo 最终定稿" : $"实时识别 · 松开{triggerName} 完成",
            Latest(text));

    public static OverlayPresentation Final(string text, bool inserted) =>
        new(
            OverlayPhase.Final,
            inserted ? "识别结果已输入" : "识别结果已复制",
            Latest(text),
            inserted);

    public static string Latest(string text)
    {
        string value = text.Trim();
        return value.Length <= MaximumVisibleCharacters
            ? value
            : value[^MaximumVisibleCharacters..];
    }
}
