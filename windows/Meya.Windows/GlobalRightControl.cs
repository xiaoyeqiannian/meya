using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;

namespace Meya.Windows;

internal sealed class GlobalRightControl : IDisposable
{
    private const int WhKeyboardLl = 13;
    private const int WmKeyDown = 0x0100;
    private const int WmKeyUp = 0x0101;
    private const int WmSysKeyDown = 0x0104;
    private const int WmSysKeyUp = 0x0105;
    private const uint LlkhfInjected = 0x10;
    private const uint VkRcontrol = 0xA3;

    private readonly HookProc _callback;
    private IntPtr _hook;
    private volatile bool _rightControlDown;
    private volatile bool _cancelledUntilRelease;

    internal event EventHandler? Pressed;
    internal event EventHandler? Released;
    internal event EventHandler? Cancelled;

    internal GlobalRightControl()
    {
        _callback = HookCallback;
        using Process process = Process.GetCurrentProcess();
        using ProcessModule? module = process.MainModule;
        IntPtr moduleHandle = GetModuleHandle(module?.ModuleName);
        _hook = SetWindowsHookEx(WhKeyboardLl, _callback, moduleHandle, 0);
        if (_hook == IntPtr.Zero)
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "无法安装全局键盘钩子");
        }
    }

    private IntPtr HookCallback(int code, IntPtr message, IntPtr data)
    {
        if (code >= 0)
        {
            KeyboardData keyboard = Marshal.PtrToStructure<KeyboardData>(data);
            bool injected = (keyboard.Flags & LlkhfInjected) != 0;
            bool down = message == (IntPtr)WmKeyDown || message == (IntPtr)WmSysKeyDown;
            bool up = message == (IntPtr)WmKeyUp || message == (IntPtr)WmSysKeyUp;
            if (!injected && keyboard.VirtualKey == VkRcontrol)
            {
                if (down && !_rightControlDown && !_cancelledUntilRelease)
                {
                    _rightControlDown = true;
                    Pressed?.Invoke(this, EventArgs.Empty);
                }
                else if (up)
                {
                    bool wasActive = _rightControlDown;
                    _rightControlDown = false;
                    _cancelledUntilRelease = false;
                    if (wasActive)
                    {
                        Released?.Invoke(this, EventArgs.Empty);
                    }
                }
            }
            else if (!injected && down && _rightControlDown && !IsModifier(keyboard.VirtualKey))
            {
                _rightControlDown = false;
                _cancelledUntilRelease = true;
                Cancelled?.Invoke(this, EventArgs.Empty);
            }
        }
        return CallNextHookEx(_hook, code, message, data);
    }

    private static bool IsModifier(uint key) => key is
        0x10 or 0xA0 or 0xA1 or
        0x11 or 0xA2 or 0xA3 or
        0x12 or 0xA4 or 0xA5 or
        0x5B or 0x5C;

    public void Dispose()
    {
        IntPtr hook = Interlocked.Exchange(ref _hook, IntPtr.Zero);
        if (hook != IntPtr.Zero)
        {
            UnhookWindowsHookEx(hook);
        }
        GC.KeepAlive(_callback);
    }

    private delegate IntPtr HookProc(int code, IntPtr message, IntPtr data);

    [StructLayout(LayoutKind.Sequential)]
    private struct KeyboardData
    {
        internal uint VirtualKey;
        internal uint ScanCode;
        internal uint Flags;
        internal uint Time;
        internal UIntPtr ExtraInfo;
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr SetWindowsHookEx(int idHook, HookProc callback, IntPtr module, uint threadId);

    [DllImport("user32.dll")]
    private static extern bool UnhookWindowsHookEx(IntPtr hook);

    [DllImport("user32.dll")]
    private static extern IntPtr CallNextHookEx(IntPtr hook, int code, IntPtr message, IntPtr data);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr GetModuleHandle(string? moduleName);
}
