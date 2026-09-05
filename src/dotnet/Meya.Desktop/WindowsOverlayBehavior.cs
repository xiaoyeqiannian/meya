using System.Runtime.InteropServices;
using Avalonia.Platform;
using Meya.UI.Views;

namespace Meya.Desktop;

internal static class WindowsOverlayBehavior
{
    private const int GwlExStyle = -20;
    private const long WsExTransparent = 0x00000020L;
    private const long WsExToolWindow = 0x00000080L;
    private const long WsExNoActivate = 0x08000000L;

    internal static void Configure(OverlayWindow window)
    {
        IPlatformHandle? handle = window.TryGetPlatformHandle();
        if (handle is null || handle.Handle == IntPtr.Zero)
        {
            return;
        }
        nint style = GetWindowLongPtr(handle.Handle, GwlExStyle);
        nint updated = style | (nint)(WsExTransparent | WsExToolWindow | WsExNoActivate);
        SetWindowLongPtr(handle.Handle, GwlExStyle, updated);
    }

    private static nint GetWindowLongPtr(nint window, int index) =>
        IntPtr.Size == 8 ? GetWindowLongPtr64(window, index) : GetWindowLong32(window, index);

    private static nint SetWindowLongPtr(nint window, int index, nint value) =>
        IntPtr.Size == 8 ? SetWindowLongPtr64(window, index, value) : SetWindowLong32(window, index, value);

    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW", SetLastError = true)]
    private static extern nint GetWindowLongPtr64(nint window, int index);

    [DllImport("user32.dll", EntryPoint = "GetWindowLongW", SetLastError = true)]
    private static extern nint GetWindowLong32(nint window, int index);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
    private static extern nint SetWindowLongPtr64(nint window, int index, nint value);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongW", SetLastError = true)]
    private static extern nint SetWindowLong32(nint window, int index, nint value);
}
