using System.Diagnostics;
using System.Runtime.InteropServices;

namespace Meya.Windows;

internal sealed record ForegroundTarget(IntPtr Window, uint ProcessId, string ProcessName)
{
    internal static ForegroundTarget? Capture()
    {
        IntPtr window = GetForegroundWindow();
        if (window == IntPtr.Zero)
        {
            return null;
        }
        GetWindowThreadProcessId(window, out uint processId);
        string name = "unknown";
        try
        {
            name = Process.GetProcessById((int)processId).ProcessName;
        }
        catch
        {
        }
        return new ForegroundTarget(window, processId, name);
    }

    internal bool IsStillForeground() => Window != IntPtr.Zero && GetForegroundWindow() == Window;

    [DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
}
