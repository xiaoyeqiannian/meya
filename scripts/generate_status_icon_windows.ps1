param(
    [string]$OutputPath = (Join-Path $PSScriptRoot '..\app\MeyaStatus.png'),
    [int]$Size = 128
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

if ($Size -lt 32) {
    throw 'Icon size must be at least 32 pixels.'
}

$zoom = 1.18
$centerX = 512.0
$centerY = 470.0
$scale = $Size / 1024.0

function Convert-Point([double]$x, [double]$y) {
    $transformedX = $centerX + ($x - $centerX) * $zoom
    $transformedY = $centerY + ($y - $centerY) * $zoom
    return [System.Drawing.PointF]::new(
        [single]($transformedX * $scale),
        [single]($Size - $transformedY * $scale))
}

function New-RoundedRectanglePath([System.Drawing.RectangleF]$rect, [single]$radius) {
    $diameter = $radius * 2
    $path = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $path.AddArc($rect.Left, $rect.Top, $diameter, $diameter, 180, 90)
    $path.AddArc($rect.Right - $diameter, $rect.Top, $diameter, $diameter, 270, 90)
    $path.AddArc($rect.Right - $diameter, $rect.Bottom - $diameter, $diameter, $diameter, 0, 90)
    $path.AddArc($rect.Left, $rect.Bottom - $diameter, $diameter, $diameter, 90, 90)
    $path.CloseFigure()
    return $path
}

function New-LeafPath([bool]$mirrored) {
    $direction = if ($mirrored) { 1.0 } else { -1.0 }
    $path = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $start = Convert-Point (512 + $direction * 35) 664
    $path.StartFigure()
    $path.AddBezier(
        $start,
        (Convert-Point (512 + $direction * 92) 749),
        (Convert-Point (512 + $direction * 155) 842),
        (Convert-Point (512 + $direction * 230) 846))
    $path.AddBezier(
        (Convert-Point (512 + $direction * 230) 846),
        (Convert-Point (512 + $direction * 270) 874),
        (Convert-Point (512 + $direction * 320) 850),
        (Convert-Point (512 + $direction * 312) 802))
    $path.AddBezier(
        (Convert-Point (512 + $direction * 312) 802),
        (Convert-Point (512 + $direction * 325) 696),
        (Convert-Point (512 + $direction * 156) 648),
        $start)
    $path.CloseFigure()
    return $path
}

function New-BodyPath {
    $path = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $start = Convert-Point 512 722
    $path.StartFigure()
    $path.AddBezier($start, (Convert-Point 688 724), (Convert-Point 792 594), (Convert-Point 772 414))
    $path.AddBezier((Convert-Point 772 414), (Convert-Point 770 262), (Convert-Point 648 206), (Convert-Point 512 206))
    $path.AddBezier((Convert-Point 512 206), (Convert-Point 376 206), (Convert-Point 254 262), (Convert-Point 252 414))
    $path.AddBezier((Convert-Point 252 414), (Convert-Point 232 594), (Convert-Point 336 724), $start)
    $path.CloseFigure()
    return $path
}

function New-RoundedBar([double]$x, [double]$width, [double]$height) {
    $center = Convert-Point ($x + $width / 2) 410
    $pixelWidth = [single]($width * $scale * $zoom)
    $pixelHeight = [single]($height * $scale * $zoom)
    $rect = [System.Drawing.RectangleF]::new(
        [single]($center.X - $pixelWidth / 2),
        [single]($center.Y - $pixelHeight / 2),
        $pixelWidth,
        $pixelHeight)
    $radius = $pixelWidth / 2
    $diameter = $radius * 2
    $path = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $path.AddArc($rect.Left, $rect.Top, $diameter, $diameter, 180, 180)
    $path.AddLine($rect.Right, $rect.Top + $radius, $rect.Right, $rect.Bottom - $radius)
    $path.AddArc($rect.Left, $rect.Bottom - $diameter, $diameter, $diameter, 0, 180)
    $path.AddLine($rect.Left, $rect.Bottom - $radius, $rect.Left, $rect.Top + $radius)
    $path.CloseFigure()
    return $path
}

$directory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$bitmap = [System.Drawing.Bitmap]::new($Size, $Size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
try {
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.Clear([System.Drawing.Color]::Transparent)
        $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality

        $backgroundColor = [System.Drawing.Color]::FromArgb(255, 22, 72, 102)
        $backgroundInset = [single]($Size * 0.035)
        $backgroundRect = [System.Drawing.RectangleF]::new(
            $backgroundInset,
            $backgroundInset,
            [single]($Size - $backgroundInset * 2),
            [single]($Size - $backgroundInset * 2))
        $backgroundPath = New-RoundedRectanglePath $backgroundRect ([single]($Size * 0.22))
        $backgroundBrush = [System.Drawing.SolidBrush]::new($backgroundColor)
        $backgroundOutline = [System.Drawing.Pen]::new(
            [System.Drawing.Color]::FromArgb(255, 3, 15, 31),
            [single]([Math]::Max(1.0, $Size * 0.018)))
        try {
            $graphics.FillPath($backgroundBrush, $backgroundPath)
            $graphics.DrawPath($backgroundOutline, $backgroundPath)
        }
        finally {
            $backgroundOutline.Dispose()
            $backgroundBrush.Dispose()
            $backgroundPath.Dispose()
        }

        $gradient = [System.Drawing.Drawing2D.LinearGradientBrush]::new(
            [System.Drawing.Rectangle]::new(0, 0, $Size, $Size),
            [System.Drawing.Color]::FromArgb(255, 194, 255, 211),
            [System.Drawing.Color]::FromArgb(255, 61, 235, 204),
            70.0)
        $outline = [System.Drawing.Pen]::new(
            [System.Drawing.Color]::FromArgb(235, 7, 31, 62),
            [single]([Math]::Max(2.0, $Size * 0.026)))
        $outline.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
        try {
            $paths = @((New-LeafPath $false), (New-LeafPath $true), (New-BodyPath))
            try {
                foreach ($path in $paths) {
                    $graphics.FillPath($gradient, $path)
                    $graphics.DrawPath($outline, $path)
                }
            }
            finally {
                foreach ($path in $paths) { $path.Dispose() }
            }
        }
        finally {
            $outline.Dispose()
            $gradient.Dispose()
        }

        $waveBrush = [System.Drawing.SolidBrush]::new($backgroundColor)
        try {
            foreach ($bar in @(
                @(326.0, 54.0, 100.0),
                @(404.0, 56.0, 165.0),
                @(484.0, 56.0, 230.0),
                @(564.0, 56.0, 165.0),
                @(644.0, 54.0, 100.0))) {
                $path = New-RoundedBar $bar[0] $bar[1] $bar[2]
                try { $graphics.FillPath($waveBrush, $path) }
                finally { $path.Dispose() }
            }
        }
        finally {
            $waveBrush.Dispose()
        }
    }
    finally {
        $graphics.Dispose()
    }
    $bitmap.Save($OutputPath, [System.Drawing.Imaging.ImageFormat]::Png)
}
finally {
    $bitmap.Dispose()
}

Write-Host "Generated status icon: $OutputPath ($Size x $Size)"
