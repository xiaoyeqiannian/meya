using Avalonia;
using Avalonia.Controls;
using Avalonia.Threading;
using Meya.Core;
using Meya.UI.ViewModels;

namespace Meya.UI.Views;

public partial class OverlayWindow : Window
{
    public OverlayWindow()
    {
        InitializeComponent();
        ViewModel = new OverlayViewModel();
        DataContext = ViewModel;
        Opened += (_, _) => NativeWindowReady?.Invoke(this);
    }

    public OverlayViewModel ViewModel { get; }

    public event Action<OverlayWindow>? NativeWindowReady;

    public Func<PixelPoint?>? AnchorPointProvider { get; set; }

    public void ShowPresentation(OverlayPresentation presentation)
    {
        ViewModel.Apply(presentation);
        MascotView.SetPhase(presentation.Phase);
        if (presentation.Phase == OverlayPhase.Hidden)
        {
            Hide();
            return;
        }

        PositionAtPrimaryWorkingArea();
        if (!IsVisible)
        {
            Show();
        }
        else
        {
            ActivatePositionOnly();
        }
    }

    public void UpdateAudioLevel(double level)
    {
        if (Dispatcher.UIThread.CheckAccess())
        {
            MascotView.UpdateAudioLevel(level);
            return;
        }
        Dispatcher.UIThread.Post(() => MascotView.UpdateAudioLevel(level));
    }

    private void PositionAtPrimaryWorkingArea()
    {
        PixelPoint? anchor = AnchorPointProvider?.Invoke();
        PixelRect? area = anchor is { } point
            ? Screens.ScreenFromPoint(point)?.WorkingArea
            : Screens.Primary?.WorkingArea;
        area ??= Screens.Primary?.WorkingArea;
        if (area is null)
        {
            return;
        }
        double scale = RenderScaling <= 0 ? 1 : RenderScaling;
        int width = (int)Math.Ceiling(Width * scale);
        int height = (int)Math.Ceiling(Height * scale);
        Position = new PixelPoint(area.Value.Right - width - 24, area.Value.Bottom - height - 24);
    }

    private void ActivatePositionOnly()
    {
        PositionAtPrimaryWorkingArea();
        InvalidateVisual();
    }
}
