import Foundation
import Carbon

guard CommandLine.arguments.count >= 2 else {
    FileHandle.standardError.write(Data("用法: register-input-method <InputMethod.app> [--select | --voice-only]\n".utf8))
    exit(2)
}

let appURL = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
let shouldSelect = CommandLine.arguments.contains("--select")
let voiceOnlyMode = CommandLine.arguments.contains("--voice-only")
let status = TISRegisterInputSource(appURL as CFURL)
guard status == noErr else {
    FileHandle.standardError.write(Data("TISRegisterInputSource 失败: \(status)\n".utf8))
    exit(1)
}

let allSources = TISCreateInputSourceList(nil, true).takeRetainedValue() as! [TISInputSource]
func stringProperty(_ source: TISInputSource, _ key: CFString) -> String? {
    let valueRef = TISGetInputSourceProperty(source, key)
    return unsafeBitCast(valueRef, to: CFString?.self) as String?
}
let sources = allSources.filter { source in
    let values = [
        stringProperty(source, kTISPropertyInputSourceID),
        stringProperty(source, kTISPropertyBundleID),
        stringProperty(source, kTISPropertyLocalizedName),
    ].compactMap { $0 }
    return values.contains { value in
        value.localizedCaseInsensitiveContains("LocalVoiceInput")
            || value.localizedCaseInsensitiveContains("com.dp.inputmethod")
            || value.localizedCaseInsensitiveContains("本地语音")
            || value.localizedCaseInsensitiveContains("听澜")
            || value.localizedCaseInsensitiveContains("麦芽")
    }
}

if voiceOnlyMode {
    // The app runs as a global Fn speech service. Keyboard composition remains
    // owned by macOS Pinyin/ABC, so ordinary typing and system switching are
    // never routed through the speech-only IMK controller.
    let pinyinSource = allSources.first { source in
        stringProperty(source, kTISPropertyInputSourceID) == "com.apple.inputmethod.SCIM.ITABC"
            || stringProperty(source, kTISPropertyInputModeID) == "com.apple.inputmethod.SCIM.ITABC"
    }
    if let pinyinSource {
        _ = TISEnableInputSource(pinyinSource)
        let selectStatus = TISSelectInputSource(pinyinSource)
        print("选择 macOS 简体拼音: \(selectStatus == noErr ? "成功" : "失败 \(selectStatus)")")
    } else {
        FileHandle.standardError.write(Data("未找到 macOS 简体拼音输入源\n".utf8))
    }
    for source in sources {
        _ = TISDisableInputSource(source)
    }
    print("MAYA 独立语音模式已启用：输入源已禁用，Fn 后台语音仍可用。")
} else {
    for source in sources {
        if [
            "com.dp.inputmethod.LocalVoiceInput.Voice",
            "com.dp.inputmethod.LocalVoiceInput.VoiceV2",
            "com.dp.inputmethod.LocalVoiceInput.VoiceV3",
        ].contains(stringProperty(source, kTISPropertyInputSourceID) ?? "") {
            _ = TISDisableInputSource(source)
        } else {
            _ = TISEnableInputSource(source)
        }
    }
}

if shouldSelect, !voiceOnlyMode, let primarySource = sources.first(where: { source in
    stringProperty(source, kTISPropertyInputSourceID) == "com.dp.inputmethod.LocalVoiceInput.VoiceV4"
}) {
    let selectStatus = TISSelectInputSource(primarySource)
    if selectStatus == noErr {
        print("选择本地语音输入模式: 成功")
    } else if let baseSource = sources.first(where: { source in
        stringProperty(source, kTISPropertyInputSourceID) == "com.dp.inputmethod.LocalVoiceInput"
    }) {
        let fallbackStatus = TISSelectInputSource(baseSource)
        print("选择输入模式失败 \(selectStatus)，改选基础输入源: \(fallbackStatus == noErr ? "成功" : "失败 \(fallbackStatus)")")
    } else {
        print("选择本地语音输入: 失败 \(selectStatus)")
    }
}

print("已注册本地语音程序，找到 \(sources.count) 个关联输入源（系统总计 \(allSources.count) 个）。")
for source in sources {
    print("- id=\(stringProperty(source, kTISPropertyInputSourceID) ?? "?") bundle=\(stringProperty(source, kTISPropertyBundleID) ?? "?") name=\(stringProperty(source, kTISPropertyLocalizedName) ?? "?")")
}
let currentSource = TISCopyCurrentKeyboardInputSource().takeRetainedValue()
print("当前键盘输入源: \(stringProperty(currentSource, kTISPropertyInputSourceID) ?? "?")")
