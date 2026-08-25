import Foundation

guard CommandLine.arguments.count == 3 else {
    fputs("usage: pack-icns <iconset-dir> <output.icns>\n", stderr)
    exit(2)
}

let iconsetURL = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
let outputURL = URL(fileURLWithPath: CommandLine.arguments[2])
let variants: [(type: String, file: String)] = [
    ("icp4", "icon_16x16.png"),
    ("icp5", "icon_32x32.png"),
    ("icp6", "icon_32x32@2x.png"),
    ("ic07", "icon_128x128.png"),
    ("ic08", "icon_256x256.png"),
    ("ic09", "icon_512x512.png"),
    ("ic10", "icon_512x512@2x.png"),
]

func appendFourCC(_ value: String, to data: inout Data) {
    data.append(contentsOf: value.utf8)
}

func appendUInt32BE(_ value: UInt32, to data: inout Data) {
    data.append(UInt8((value >> 24) & 0xff))
    data.append(UInt8((value >> 16) & 0xff))
    data.append(UInt8((value >> 8) & 0xff))
    data.append(UInt8(value & 0xff))
}

var elements = Data()
for variant in variants {
    let pngURL = iconsetURL.appendingPathComponent(variant.file)
    let png = try Data(contentsOf: pngURL)
    appendFourCC(variant.type, to: &elements)
    appendUInt32BE(UInt32(png.count + 8), to: &elements)
    elements.append(png)
}

var icns = Data()
appendFourCC("icns", to: &icns)
appendUInt32BE(UInt32(elements.count + 8), to: &icns)
icns.append(elements)
try icns.write(to: outputURL, options: .atomic)
