namespace Meya.Windows;

internal sealed class OverlayForm : Form
{
    private readonly Label _statusLabel;
    private readonly Label _draftLabel;

    internal OverlayForm()
    {
        FormBorderStyle = FormBorderStyle.None;
        ShowInTaskbar = false;
        TopMost = true;
        StartPosition = FormStartPosition.Manual;
        BackColor = Color.FromArgb(32, 32, 36);
        ForeColor = Color.White;
        Size = new Size(360, 78);
        Padding = new Padding(14, 10, 14, 10);

        _statusLabel = new Label
        {
            AutoSize = false,
            Dock = DockStyle.Top,
            Height = 22,
            ForeColor = Color.FromArgb(190, 210, 220),
            TextAlign = ContentAlignment.MiddleLeft,
            Font = new Font((SystemFonts.MessageBoxFont ?? SystemFonts.DefaultFont).FontFamily, 9, FontStyle.Regular),
        };
        _draftLabel = new Label
        {
            AutoSize = false,
            Dock = DockStyle.Fill,
            ForeColor = Color.White,
            TextAlign = ContentAlignment.MiddleLeft,
            AutoEllipsis = false,
            Font = new Font((SystemFonts.MessageBoxFont ?? SystemFonts.DefaultFont).FontFamily, 11, FontStyle.Regular),
        };
        Controls.Add(_draftLabel);
        Controls.Add(_statusLabel);
    }

    protected override bool ShowWithoutActivation => true;

    protected override CreateParams CreateParams
    {
        get
        {
            const int WsExTransparent = 0x00000020;
            const int WsExToolWindow = 0x00000080;
            const int WsExNoActivate = 0x08000000;
            CreateParams value = base.CreateParams;
            value.ExStyle |= WsExTransparent | WsExToolWindow | WsExNoActivate;
            return value;
        }
    }

    internal void ShowState(string text)
    {
        _statusLabel.Text = string.Empty;
        _statusLabel.Visible = false;
        _draftLabel.Text = text;
        Size = new Size(320, 54);
        ShowAtWorkingArea();
    }

    internal void ShowRecording(bool streamingAvailable)
    {
        _statusLabel.Visible = false;
        _statusLabel.Text = string.Empty;
        _draftLabel.Text = streamingAvailable
            ? "正在听 · 松开右 Ctrl 完成"
            : "正在录音 · 松开右 Ctrl 完成";
        Size = new Size(320, 54);
        ShowAtWorkingArea();
    }

    internal void ShowDraft(string text, bool finalizing = false)
    {
        _statusLabel.Visible = true;
        _statusLabel.Text = finalizing
            ? "正在使用 SeACo 最终定稿"
            : "实时识别 · 松开右 Ctrl 完成";
        _draftLabel.Text = LatestText(text);
        Size = new Size(480, 120);
        ShowAtWorkingArea();
    }

    internal void ShowRecognizing(string? draft)
    {
        if (string.IsNullOrWhiteSpace(draft))
        {
            _statusLabel.Visible = false;
            _statusLabel.Text = string.Empty;
            _draftLabel.Text = "正在使用 SeACo 最终定稿";
            Size = new Size(320, 54);
        }
        else
        {
            ShowDraft(draft, finalizing: true);
        }
        ShowAtWorkingArea();
    }

    internal void ShowFinal(string text, bool inserted)
    {
        _statusLabel.Visible = true;
        _statusLabel.Text = inserted ? "识别结果已输入" : "识别结果已复制";
        _draftLabel.Text = LatestText(text);
        Size = new Size(480, 120);
        ShowAtWorkingArea();
    }

    internal void HideState() => Hide();

    private static string LatestText(string text)
    {
        const int MaximumVisibleCharacters = 96;
        string value = text.Trim();
        return value.Length <= MaximumVisibleCharacters
            ? value
            : value[^MaximumVisibleCharacters..];
    }

    private void ShowAtWorkingArea()
    {
        Rectangle area = Screen.FromPoint(Cursor.Position).WorkingArea;
        Location = new Point(area.Right - Width - 24, area.Bottom - Height - 24);
        if (!Visible)
        {
            Show();
        }
        else
        {
            Invalidate();
        }
    }
}
