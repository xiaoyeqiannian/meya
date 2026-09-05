using System.Runtime.InteropServices;
using System.Text;

namespace Meya.Windows;

internal static class TextInjector
{
    private const uint InputKeyboard = 1;
    private const uint KeyEventKeyUp = 0x0002;
    private const uint KeyEventUnicode = 0x0004;
    private const ushort VkControl = 0x11;
    private const ushort VkV = 0x56;
    private const uint CfUnicodeText = 13;
    private const uint GmemMoveable = 0x0002;
    private const uint GmemZeroInit = 0x0040;

    internal static bool Insert(string text)
    {
        if (string.IsNullOrEmpty(text))
        {
            return true;
        }
        if (Copy(text) && TryPaste())
        {
            return true;
        }
        return TryUnicode(text);
    }

    internal static bool Copy(string text) => !string.IsNullOrEmpty(text) && TrySetClipboard(text);

    private static bool TrySetClipboard(string text)
    {
        byte[] bytes = Encoding.Unicode.GetBytes(text + '\0');
        for (int attempt = 0; attempt < 5; attempt++)
        {
            if (!OpenClipboard(IntPtr.Zero))
            {
                Thread.Sleep(40 * (attempt + 1));
                continue;
            }

            nint memory = IntPtr.Zero;
            bool transferred = false;
            try
            {
                if (!EmptyClipboard())
                {
                    continue;
                }
                memory = GlobalAlloc(GmemMoveable | GmemZeroInit, (nuint)bytes.Length);
                if (memory == IntPtr.Zero)
                {
                    continue;
                }
                nint pointer = GlobalLock(memory);
                if (pointer == IntPtr.Zero)
                {
                    continue;
                }
                try
                {
                    Marshal.Copy(bytes, 0, pointer, bytes.Length);
                }
                finally
                {
                    GlobalUnlock(memory);
                }
                transferred = SetClipboardData(CfUnicodeText, memory) != IntPtr.Zero;
                if (transferred)
                {
                    memory = IntPtr.Zero;
                    return true;
                }
            }
            finally
            {
                if (!transferred && memory != IntPtr.Zero)
                {
                    GlobalFree(memory);
                }
                CloseClipboard();
            }
            Thread.Sleep(40 * (attempt + 1));
        }
        return false;
    }

    private static bool TryPaste()
    {
        Input[] inputs =
        [
            Key(VkControl, 0),
            Key(VkV, 0),
            Key(VkV, KeyEventKeyUp),
            Key(VkControl, KeyEventKeyUp),
        ];
        return SendInput((uint)inputs.Length, inputs, Marshal.SizeOf<Input>()) == inputs.Length;
    }

    private static bool TryUnicode(string text)
    {
        List<Input> inputs = [];
        foreach (char value in text)
        {
            inputs.Add(Unicode(value, 0));
            inputs.Add(Unicode(value, KeyEventKeyUp));
        }
        return SendInputs(inputs);
    }

    private static bool SendInputs(List<Input> inputs)
    {
        if (inputs.Count == 0)
        {
            return true;
        }
        Input[] array = inputs.ToArray();
        return SendInput((uint)array.Length, array, Marshal.SizeOf<Input>()) == array.Length;
    }

    private static Input Key(ushort key, uint flags) => new()
    {
        Type = InputKeyboard,
        Union = new InputUnion { Keyboard = new KeyboardInput { VirtualKey = key, Flags = flags } },
    };

    private static Input Unicode(char value, uint flags) => new()
    {
        Type = InputKeyboard,
        Union = new InputUnion
        {
            Keyboard = new KeyboardInput
            {
                ScanCode = value,
                Flags = flags | KeyEventUnicode,
            },
        },
    };

    [StructLayout(LayoutKind.Sequential)]
    private struct Input
    {
        internal uint Type;
        internal InputUnion Union;
    }

    [StructLayout(LayoutKind.Explicit)]
    private struct InputUnion
    {
        [FieldOffset(0)] internal MouseInput Mouse;
        [FieldOffset(0)] internal KeyboardInput Keyboard;
        [FieldOffset(0)] internal HardwareInput Hardware;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct KeyboardInput
    {
        internal ushort VirtualKey;
        internal ushort ScanCode;
        internal uint Flags;
        internal uint Time;
        internal UIntPtr ExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MouseInput
    {
        internal int Dx;
        internal int Dy;
        internal uint MouseData;
        internal uint Flags;
        internal uint Time;
        internal UIntPtr ExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct HardwareInput
    {
        internal uint Message;
        internal ushort ParamLow;
        internal ushort ParamHigh;
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern uint SendInput(uint count, Input[] inputs, int size);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool OpenClipboard(nint owner);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool CloseClipboard();

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool EmptyClipboard();

    [DllImport("user32.dll", SetLastError = true)]
    private static extern nint SetClipboardData(uint format, nint memory);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern nint GlobalAlloc(uint flags, nuint bytes);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern nint GlobalLock(nint memory);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GlobalUnlock(nint memory);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern nint GlobalFree(nint memory);
}
