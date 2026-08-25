import AVFoundation
import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

let arguments = CommandLine.arguments
guard arguments.count >= 3 else {
    fail("用法: recorder <输出.wav> <秒数>")
}

let outputURL = URL(fileURLWithPath: arguments[1]).standardizedFileURL
guard let duration = Double(arguments[2]), duration > 0, duration <= 300 else {
    fail("录音时长必须在 0到300 秒之间")
}

try? FileManager.default.removeItem(at: outputURL)
try? FileManager.default.createDirectory(
    at: outputURL.deletingLastPathComponent(),
    withIntermediateDirectories: true
)

let settings: [String: Any] = [
    AVFormatIDKey: Int(kAudioFormatLinearPCM),
    AVSampleRateKey: 16_000.0,
    AVNumberOfChannelsKey: 1,
    AVLinearPCMBitDepthKey: 16,
    AVLinearPCMIsFloatKey: false,
    AVLinearPCMIsBigEndianKey: false,
]

do {
    let recorder = try AVAudioRecorder(url: outputURL, settings: settings)
    guard recorder.prepareToRecord(), recorder.record() else {
        fail("无法启动麦克风录音。请检查终端或当前启动应用的麦克风权限。")
    }
    let durationText = String(format: "%.1f", duration)
    print("正在录音 " + durationText + " 秒，请开始说话…")
    RunLoop.current.run(until: Date(timeIntervalSinceNow: duration))
    recorder.stop()

    let attributes = try FileManager.default.attributesOfItem(atPath: outputURL.path)
    let size = attributes[.size] as? NSNumber ?? 0
    guard size.intValue > 44 else {
        fail("录音文件为空，请检查麦克风权限。")
    }
    print("录音已保存: \(outputURL.path)")
} catch {
    fail("录音失败: \(error.localizedDescription)")
}
