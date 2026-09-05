using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Platform;
using Avalonia.Threading;
using Meya.Core;

namespace Meya.UI.Views;

public sealed class MayaMascotView : Control
{
    private enum MascotState
    {
        Listening,
        Processing,
        Complete,
    }

    private static readonly IBrush CoreBrush = new SolidColorBrush(Color.Parse("#FA051326"));
    private static readonly IBrush RingBrush = new SolidColorBrush(Color.Parse("#C747EDCF"));
    private static readonly IBrush ActivityBrush = new SolidColorBrush(Color.Parse("#FFFFB057"));

    private readonly Bitmap _mascot;
    private readonly DispatcherTimer _timer;
    private MascotState _state = MascotState.Listening;
    private double _targetLevel = 0.06;
    private double _level = 0.06;
    private double _phase;

    public MayaMascotView()
    {
        using Stream stream = AssetLoader.Open(
            new Uri("avares://Meya.UI/Assets/MayaMascot3D.Overlay.png"));
        _mascot = new Bitmap(stream);
        RenderOptions.SetBitmapInterpolationMode(this, BitmapInterpolationMode.HighQuality);
        _timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1.0 / 30.0) };
        _timer.Tick += OnAnimationTick;
    }

    public void SetPhase(OverlayPhase phase)
    {
        MascotState state = phase switch
        {
            OverlayPhase.Finalizing => MascotState.Processing,
            OverlayPhase.Final => MascotState.Complete,
            _ => MascotState.Listening,
        };
        if (_state != state)
        {
            _state = state;
            _phase = 0;
            _targetLevel = state == MascotState.Listening ? 0.06 : 0.10;
            _level = _targetLevel;
        }

        if (phase == OverlayPhase.Hidden)
        {
            _timer.Stop();
        }
        else if (!_timer.IsEnabled)
        {
            _timer.Start();
        }
        InvalidateVisual();
    }

    public void UpdateAudioLevel(double level)
    {
        if (_state != MascotState.Listening)
        {
            return;
        }
        _targetLevel = Math.Clamp(level, 0.03, 1.0);
    }

    public override void Render(DrawingContext context)
    {
        base.Render(context);
        Rect bounds = new(0, 0, Bounds.Width, Bounds.Height);
        if (bounds.Width <= 0 || bounds.Height <= 0)
        {
            return;
        }

        context.DrawImage(_mascot, new Rect(_mascot.Size), bounds);

        double size = Math.Min(bounds.Width, bounds.Height);
        Point center = new(bounds.Center.X, bounds.Y + size * 0.695);
        double radius = size * 0.108;
        context.DrawEllipse(
            CoreBrush,
            new Pen(RingBrush, Math.Max(1, size * 0.012)),
            center,
            radius,
            radius);

        switch (_state)
        {
            case MascotState.Processing:
                DrawProcessing(context, center, radius, size);
                break;
            case MascotState.Complete:
                DrawComplete(context, center, radius, size);
                break;
            default:
                DrawListening(context, center, radius, size);
                break;
        }
    }

    private void OnAnimationTick(object? sender, EventArgs args)
    {
        _phase += _state == MascotState.Processing ? 0.09 : 0.18;
        if (_state == MascotState.Listening)
        {
            _level = _level * 0.58 + _targetLevel * 0.42;
            _targetLevel = Math.Max(0.04, _targetLevel * 0.94);
        }
        InvalidateVisual();
    }

    private void DrawListening(DrawingContext context, Point center, double radius, double size)
    {
        double halfWidth = radius * 0.62;
        double amplitude = radius * (0.16 + Math.Min(1, _level) * 0.50);
        StreamGeometry wave = new();
        using (StreamGeometryContext geometry = wave.Open())
        {
            for (int index = 0; index <= 6; index++)
            {
                double progress = index / 6.0;
                double x = center.X - halfWidth + progress * halfWidth * 2;
                double shape = Math.Sin((progress * 3 + _phase * 0.55) * Math.PI);
                double envelope = Math.Max(0.28, 1 - Math.Abs(progress - 0.5) * 1.25);
                double y = center.Y + shape * amplitude * envelope;
                if (index == 0)
                {
                    geometry.BeginFigure(new Point(x, y), false);
                }
                else
                {
                    geometry.LineTo(new Point(x, y));
                }
            }
            geometry.EndFigure(false);
        }
        context.DrawGeometry(
            null,
            new Pen(ActivityBrush, Math.Max(1.5, size * 0.027), lineCap: PenLineCap.Round, lineJoin: PenLineJoin.Round),
            wave);
    }

    private void DrawProcessing(DrawingContext context, Point center, double radius, double size)
    {
        for (int index = -1; index <= 1; index++)
        {
            double bounce = 0.72 + 0.28 * Math.Sin(_phase * 2 + index * 1.4);
            double dotRadius = size * 0.014 * bounce;
            context.DrawEllipse(
                ActivityBrush,
                null,
                new Point(center.X + index * radius * 0.43, center.Y),
                dotRadius,
                dotRadius);
        }
    }

    private static void DrawComplete(DrawingContext context, Point center, double radius, double size)
    {
        StreamGeometry check = new();
        using (StreamGeometryContext geometry = check.Open())
        {
            geometry.BeginFigure(
                new Point(center.X - radius * 0.46, center.Y),
                false);
            geometry.LineTo(new Point(center.X - radius * 0.10, center.Y + radius * 0.32));
            geometry.LineTo(new Point(center.X + radius * 0.52, center.Y - radius * 0.40));
            geometry.EndFigure(false);
        }
        context.DrawGeometry(
            null,
            new Pen(ActivityBrush, Math.Max(1.7, size * 0.035), lineCap: PenLineCap.Round, lineJoin: PenLineJoin.Round),
            check);
    }
}
