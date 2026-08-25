import Foundation
import Carbon

func stringProperty(_ source: TISInputSource, _ key: CFString) -> String? {
    let valueRef = TISGetInputSourceProperty(source, key)
    return unsafeBitCast(valueRef, to: CFString?.self) as String?
}

func boolProperty(_ source: TISInputSource, _ key: CFString) -> Bool? {
    let valueRef = TISGetInputSourceProperty(source, key)
    return unsafeBitCast(valueRef, to: CFBoolean?.self).map(CFBooleanGetValue)
}

func describeProperty(_ source: TISInputSource, _ key: CFString) -> String {
    guard let value = TISGetInputSourceProperty(source, key) else { return "nil" }
    let object = unsafeBitCast(value, to: CFTypeRef.self)
    return CFCopyDescription(object) as String? ?? "nil"
}

let allSources = TISCreateInputSourceList(nil, true).takeRetainedValue() as! [TISInputSource]
for source in allSources {
    let id = stringProperty(source, kTISPropertyInputSourceID) ?? ""
    guard id.contains("LocalVoiceInput") || id.contains("doubaoime") else { continue }
    print("id=\(id)")
    print("  enabled=\(boolProperty(source, kTISPropertyInputSourceIsEnabled).map(String.init) ?? "nil")")
    print("  selectCapable=\(boolProperty(source, kTISPropertyInputSourceIsSelectCapable).map(String.init) ?? "nil")")
    print("  category=\(stringProperty(source, kTISPropertyInputSourceCategory) ?? "nil")")
    print("  type=\(stringProperty(source, kTISPropertyInputSourceType) ?? "nil")")
    print("  mode=\(stringProperty(source, kTISPropertyInputModeID) ?? "nil")")
    print("  localizedName=\(describeProperty(source, kTISPropertyLocalizedName))")
    print("  bundleID=\(describeProperty(source, kTISPropertyBundleID))")
    print("  languages=\(describeProperty(source, kTISPropertyInputSourceLanguages))")
    print("  asciiCapable=\(describeProperty(source, kTISPropertyInputSourceIsASCIICapable))")
    print("  iconRef=\(describeProperty(source, kTISPropertyIconRef))")
    if let value = TISGetInputSourceProperty(source, kTISPropertyIconImageURL) {
        let object = unsafeBitCast(value, to: CFTypeRef.self)
        print("  iconImageURL=\(CFCopyDescription(object) as String? ?? "nil")")
    } else {
        print("  iconImageURL=nil")
    }
}
