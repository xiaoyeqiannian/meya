using System.Runtime.InteropServices;
using Avalonia;
using Avalonia.Platform;
using Meya.Core;
using Meya.UI.Views;

namespace Meya.Windows;

internal sealed class OverlayForm : IDisposable
{
    private readonly OverlayWindow _window = new();

    internal OverlayForm()
    {
        _window.AnchorPointProvider = CursorPoint;
        _window.NativeWindowReady += ConfigureNativeWindow;
    }

    internal void ShowState(string text) =>
        _window.ShowPresentation(OverlayPresentation.Message(text));

    internal void ShowRecording(bool streamingAvailable) =>
        _window.ShowPresentation(OverlayPresentation.Recording("右 Ctrl", streamingAvailable));

    internal void ShowDraft(string text, bool finalizing = false) =>
        _window.ShowPresentation(OverlayPresentation.Draft(text, "右 Ctrl", finalizing));

    internal void ShowRecognizing(string? draft) =>
        _window.ShowPresentation(string.IsNullOrWhiteSpace(draft)
            ? OverlayPresentation.Message("正在使用 SeACo 最终定稿")
            : OverlayPresentation.Draft(draft, "右 Ctrl", finalizing: true));

    internal void ShowFinal(string text, bool inserted) =>
        _window.ShowPresentation(OverlayPresentation.Final(text, inserted));

    internal void UpdateAudioLevel(float level) =>
        _window.UpdateAudioLevel(level);

    internal void HideState() =>
        _window.ShowPresentation(OverlayPresentation.Hidden());

    public void Dispose()
    {
        _window.NativeWindowReady -= ConfigureNativeWindow;
        _window.Close();
    }

    private static PixelPoint? CursorPoint()
    {
        return GetCursorPos(out NativePoint point)
            ? new PixelPoint(point.X, point.Y)
            : null;
    }

    private static void ConfigureNativeWindow(OverlayWindow window)
    {
        const int GwlExStyle = -20;
        const long WsExTransparent = 0x00000020L;
        const long WsExToolWindow = 0x00000080L;
        const long WsExNoActivate = 0x08000000L;
        const int DwmwaWindowCornerPreference = 33;
        const int DwmwaBorderColor = 34;
        const int DwmwcpDoNotRound = 1;
        int dwmColorNone = unchecked((int)0xFFFFFFFE);
        IPlatformHandle? handle = window.TryGetPlatformHandle();
        if (handle is null || handle.Handle == IntPtr.Zero)
        {
            return;
        }
        nint style = GetWindowLongPtr(handle.Handle, GwlExStyle);
        SetWindowLongPtr(
            handle.Handle,
            GwlExStyle,
            style | (nint)(WsExTransparent | WsExToolWindow | WsExNoActivate));

        int cornerPreference = DwmwcpDoNotRound;
        _ = DwmSetWindowAttribute(
            handle.Handle,
            DwmwaWindowCornerPreference,
            ref cornerPreference,
            sizeof(int));
        _ = DwmSetWindowAttribute(
            handle.Handle,
            DwmwaBorderColor,
            ref dwmColorNone,
            sizeof(int));
    }

    private static nint GetWindowLongPtr(nint window, int index) =>
        IntPtr.Size == 8 ? GetWindowLongPtr64(window, index) : GetWindowLong32(window, index);

    private static nint SetWindowLongPtr(nint window, int index, nint value) =>
        IntPtr.Size == 8 ? SetWindowLongPtr64(window, index, value) : SetWindowLong32(window, index, value);

    [StructLayout(LayoutKind.Sequential)]
    private struct NativePoint
    {
        internal int X;
        internal int Y;
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool GetCursorPos(out NativePoint point);

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(
        nint window,
        int attribute,
        ref int attributeValue,
        int attributeSize);


    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW", SetLastError = true)]
    private static extern nint GetWindowLongPtr64(nint window, int index);

    [DllImport("user32.dll", EntryPoint = "GetWindowLongW", SetLastError = true)]
    private static extern nint GetWindowLong32(nint window, int index);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
    private static extern nint SetWindowLongPtr64(nint window, int index, nint value);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongW", SetLastError = true)]
    private static extern nint SetWindowLong32(nint window, int index, nint value);
}
