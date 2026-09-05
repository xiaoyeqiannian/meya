using System.Runtime.InteropServices;

namespace Meya.Windows;

internal static class TextInjector
{
    private const uint InputKeyboard = 1;
    private const uint KeyEventKeyUp = 0x0002;
    private const uint KeyEventUnicode = 0x0004;
    private const ushort VkControl = 0x11;
    private const ushort VkV = 0x56;

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
        for (int attempt = 0; attempt < 5; attempt++)
        {
            try
            {
                Clipboard.SetText(text, TextDataFormat.UnicodeText);
                return true;
            }
            catch (ExternalException)
            {
                Thread.Sleep(40 * (attempt + 1));
            }
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
}
