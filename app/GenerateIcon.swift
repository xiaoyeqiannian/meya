import AppKit
import Foundation

guard CommandLine.arguments.count >= 2 else {
    fputs("usage: generate-icon <output.{png|tiff|pdf}> [size] [app|template] [asset.png]\n", stderr)
    exit(2)
}

let outputURL = URL(fileURLWithPath: CommandLine.arguments[1])
let pixelSize = CGFloat(CommandLine.arguments.dropFirst(2).first.flatMap(Int.init) ?? 1024)
let renderingMode = CommandLine.arguments.dropFirst(3).first ?? "app"
let assetURL = CommandLine.arguments.dropFirst(4).first.map { URL(fileURLWithPath: $0) }

guard pixelSize > 0, renderingMode == "app" || renderingMode == "template" else {
    fputs("size must be positive and mode must be app or template\n", stderr)
    exit(2)
}

let canvas = NSSize(width: pixelSize, height: pixelSize)
let scale = pixelSize / 1024
let isTemplate = renderingMode == "template"
let markZoom: CGFloat = 1.18
let markCenter = NSPoint(x: 512, y: 470)

func canvasPoint(_ x: CGFloat, _ y: CGFloat) -> NSPoint {
    NSPoint(x: x * scale, y: y * scale)
}

func point(_ x: CGFloat, _ y: CGFloat) -> NSPoint {
    canvasPoint(
        markCenter.x + (x - markCenter.x) * markZoom,
        markCenter.y + (y - markCenter.y) * markZoom
    )
}

func rect(_ x: CGFloat, _ y: CGFloat, _ width: CGFloat, _ height: CGFloat) -> NSRect {
    let origin = point(x, y)
    return NSRect(
        x: origin.x,
        y: origin.y,
        width: width * scale * markZoom,
        height: height * scale * markZoom
    )
}

func rawRect(_ x: CGFloat, _ y: CGFloat, _ width: CGFloat, _ height: CGFloat) -> NSRect {
    NSRect(x: x * scale, y: y * scale, width: width * scale, height: height * scale)
}

func roundedRect(_ x: CGFloat, _ y: CGFloat, _ width: CGFloat, _ height: CGFloat, _ radius: CGFloat) -> NSBezierPath {
    NSBezierPath(roundedRect: rawRect(x, y, width, height), xRadius: radius * scale, yRadius: radius * scale)
}

func clearCanvas() {
    guard let context = NSGraphicsContext.current else { return }
    context.saveGraphicsState()
    context.compositingOperation = .copy
    NSColor.clear.setFill()
    NSBezierPath(rect: NSRect(origin: .zero, size: canvas)).fill()
    context.restoreGraphicsState()
}

func leafPath(mirrored: Bool) -> NSBezierPath {
    let direction: CGFloat = mirrored ? 1 : -1
    let leaf = NSBezierPath()
    leaf.move(to: point(512 + direction * 35, 664))
    leaf.curve(
        to: point(512 + direction * 230, 846),
        controlPoint1: point(512 + direction * 92, 749),
        controlPoint2: point(512 + direction * 155, 842)
    )
    leaf.curve(
        to: point(512 + direction * 312, 802),
        controlPoint1: point(512 + direction * 270, 874),
        controlPoint2: point(512 + direction * 320, 850)
    )
    leaf.curve(
        to: point(512 + direction * 35, 664),
        controlPoint1: point(512 + direction * 325, 696),
        controlPoint2: point(512 + direction * 156, 648)
    )
    leaf.close()
    return leaf
}

func bodyPath() -> NSBezierPath {
    let body = NSBezierPath()
    body.move(to: point(512, 722))
    body.curve(to: point(772, 414), controlPoint1: point(688, 724), controlPoint2: point(792, 594))
    body.curve(to: point(512, 206), controlPoint1: point(770, 262), controlPoint2: point(648, 206))
    body.curve(to: point(252, 414), controlPoint1: point(376, 206), controlPoint2: point(254, 262))
    body.curve(to: point(512, 722), controlPoint1: point(232, 594), controlPoint2: point(336, 724))
    body.close()
    return body
}

func drawMarkTemplate() {
    NSColor.black.setFill()
    leafPath(mirrored: false).fill()
    leafPath(mirrored: true).fill()

    let template = NSBezierPath()
    template.windingRule = .evenOdd
    template.append(bodyPath())

    // PDF export does not preserve destination-out compositing reliably.
    // Append the waveform as nested subpaths and use even-odd filling so the
    // holes remain transparent in both PDF and bitmap output.
    let centerY: CGFloat = 410
    let bars: [(x: CGFloat, width: CGFloat, height: CGFloat)] = [
        (326, 54, 100),
        (404, 56, 165),
        (484, 56, 230),
        (564, 56, 165),
        (644, 54, 100),
    ]
    for bar in bars {
        let barRect = rect(bar.x, centerY - bar.height / 2, bar.width, bar.height)
        template.append(NSBezierPath(
            roundedRect: barRect,
            xRadius: bar.width * scale * markZoom / 2,
            yRadius: bar.width * scale * markZoom / 2
        ))
    }

    template.fill()
}

func drawMarkApp() {
    let mint = NSGradient(colors: [
        NSColor(calibratedRed: 0.74, green: 1.0, blue: 0.82, alpha: 1),
        NSColor(calibratedRed: 0.24, green: 0.92, blue: 0.80, alpha: 1),
    ])!

    for mirrored in [false, true] {
        mint.draw(in: leafPath(mirrored: mirrored), angle: mirrored ? 28 : 152)
    }
    mint.draw(in: bodyPath(), angle: -78)

    NSColor(calibratedRed: 0.025, green: 0.11, blue: 0.23, alpha: 1).setFill()
    NSBezierPath(ovalIn: rect(374, 492, 70, 98)).fill()
    NSBezierPath(ovalIn: rect(580, 492, 70, 98)).fill()
    NSColor(calibratedWhite: 1, alpha: 0.92).setFill()
    NSBezierPath(ovalIn: rect(391, 512, 19, 19)).fill()
    NSBezierPath(ovalIn: rect(597, 512, 19, 19)).fill()

    NSColor(calibratedRed: 0.018, green: 0.09, blue: 0.19, alpha: 1).setFill()
    NSBezierPath(ovalIn: rect(410, 260, 204, 204)).fill()
    NSColor(calibratedRed: 0.25, green: 0.93, blue: 0.82, alpha: 0.9).setStroke()
    let coreRing = NSBezierPath(ovalIn: rect(424, 274, 176, 176))
    coreRing.lineWidth = 12 * scale * markZoom
    coreRing.stroke()

    NSColor(calibratedRed: 1.0, green: 0.72, blue: 0.36, alpha: 1).setFill()
    let sound = NSBezierPath()
    sound.move(to: point(455, 362))
    sound.curve(to: point(493, 362), controlPoint1: point(470, 362), controlPoint2: point(474, 324))
    sound.curve(to: point(531, 362), controlPoint1: point(510, 302), controlPoint2: point(514, 302))
    sound.curve(to: point(569, 362), controlPoint1: point(550, 324), controlPoint2: point(554, 362))
    sound.curve(to: point(531, 362), controlPoint1: point(554, 362), controlPoint2: point(550, 400))
    sound.curve(to: point(493, 362), controlPoint1: point(514, 422), controlPoint2: point(510, 422))
    sound.curve(to: point(455, 362), controlPoint1: point(474, 400), controlPoint2: point(470, 362))
    sound.close()
    sound.fill()
}

func drawRabbitAsset() -> Bool {
    guard let assetURL,
          let image = NSImage(contentsOf: assetURL),
          let representation = image.bestRepresentation(
              for: NSRect(origin: .zero, size: canvas),
              context: nil,
              hints: nil
          )
    else {
        return false
    }

    let inset = pixelSize * 0.035
    let destination = NSRect(
        x: inset,
        y: inset,
        width: pixelSize - inset * 2,
        height: pixelSize - inset * 2
    )
    representation.draw(
        in: destination,
        from: NSRect(origin: .zero, size: representation.size),
        operation: .sourceOver,
        fraction: 1,
        respectFlipped: false,
        hints: nil
    )
    return true
}

func drawIcon() {
    NSGraphicsContext.current?.imageInterpolation = .high
    NSGraphicsContext.current?.shouldAntialias = true
    clearCanvas()

    if isTemplate {
        drawMarkTemplate()
        return
    }

    if !drawRabbitAsset() {
        let background = roundedRect(28, 28, 968, 968, 224)
        NSGradient(colors: [
            NSColor(calibratedRed: 0.035, green: 0.075, blue: 0.20, alpha: 1),
            NSColor(calibratedRed: 0.015, green: 0.025, blue: 0.075, alpha: 1),
        ])!.draw(in: background, angle: -90)
        drawMarkApp()
    }
}

let data: Data
switch outputURL.pathExtension.lowercased() {
case "pdf":
    var mediaBox = CGRect(origin: .zero, size: canvas)
    guard let consumer = CGDataConsumer(url: outputURL as CFURL),
          let context = CGContext(consumer: consumer, mediaBox: &mediaBox, nil) else {
        exit(1)
    }
    context.beginPDFPage(nil)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(cgContext: context, flipped: false)
    drawIcon()
    NSGraphicsContext.restoreGraphicsState()
    context.endPDFPage()
    context.closePDF()
    exit(0)

case "png", "tif", "tiff":
    guard let bitmap = NSBitmapImageRep(
        bitmapDataPlanes: nil,
        pixelsWide: Int(pixelSize),
        pixelsHigh: Int(pixelSize),
        bitsPerSample: 8,
        samplesPerPixel: 4,
        hasAlpha: true,
        isPlanar: false,
        colorSpaceName: .deviceRGB,
        bytesPerRow: 0,
        bitsPerPixel: 0
    ), let graphicsContext = NSGraphicsContext(bitmapImageRep: bitmap) else {
        exit(1)
    }

    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = graphicsContext
    drawIcon()
    graphicsContext.flushGraphics()
    NSGraphicsContext.restoreGraphicsState()

    if outputURL.pathExtension.lowercased() == "png" {
        guard let png = bitmap.representation(using: .png, properties: [:]) else { exit(1) }
        data = png
    } else {
        guard let tiff = bitmap.tiffRepresentation else { exit(1) }
        data = tiff
    }

default:
    fputs("unsupported output format: \(outputURL.pathExtension)\n", stderr)
    exit(2)
}

try data.write(to: outputURL, options: .atomic)
