using System.ComponentModel;
using System.Runtime.CompilerServices;
using Meya.Core;

namespace Meya.UI.ViewModels;

public sealed class OverlayViewModel : INotifyPropertyChanged
{
    private OverlayPresentation _presentation = OverlayPresentation.Hidden();

    public event PropertyChangedEventHandler? PropertyChanged;

    public string Status => _presentation.Status;
    public string Text => _presentation.Text;
    public bool HasStatus => !string.IsNullOrWhiteSpace(_presentation.Status);
    public OverlayPhase Phase => _presentation.Phase;

    public void Apply(OverlayPresentation presentation)
    {
        _presentation = presentation;
        OnPropertyChanged(nameof(Status));
        OnPropertyChanged(nameof(Text));
        OnPropertyChanged(nameof(HasStatus));
        OnPropertyChanged(nameof(Phase));
    }

    private void OnPropertyChanged([CallerMemberName] string? name = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
}
