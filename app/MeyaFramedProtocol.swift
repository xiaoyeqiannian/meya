import Foundation

enum MeyaFrameType: UInt8 {
    case control = 1
    case audioPCM16 = 2
    case event = 3
    case error = 4
    case heartbeat = 5
}

struct MeyaFrame: Equatable {
    static let version: UInt8 = 2
    static let headerSize = 36
    static let maximumPayload = 16 * 1024 * 1024
    static let zeroSession = UUID(uuid: (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0))

    let type: MeyaFrameType
    let flags: UInt16
    let session: UUID
    let sequence: UInt64
    let payload: Data

    init(
        type: MeyaFrameType,
        flags: UInt16 = 0,
        session: UUID = MeyaFrame.zeroSession,
        sequence: UInt64 = 0,
        payload: Data = Data()
    ) {
        self.type = type
        self.flags = flags
        self.session = session
        self.sequence = sequence
        self.payload = payload
    }

    func encoded() throws -> Data {
        guard payload.count <= Self.maximumPayload else {
            throw MeyaFrameError.payloadTooLarge
        }
        var data = Data("MEYA".utf8)
        data.append(Self.version)
        data.append(type.rawValue)
        data.appendBigEndian(flags)
        data.appendBigEndian(UInt32(payload.count))
        var rawUUID = session.uuid
        withUnsafeBytes(of: &rawUUID) { data.append(contentsOf: $0) }
        data.appendBigEndian(sequence)
        data.append(payload)
        return data
    }
}

enum MeyaFrameError: LocalizedError {
    case payloadTooLarge
    case unsupportedVersion(UInt8)
    case unknownType(UInt8)

    var errorDescription: String? {
        switch self {
        case .payloadTooLarge: return "IPC 消息超过 16 MiB"
        case .unsupportedVersion(let version): return "不支持的 IPC 协议版本：\(version)"
        case .unknownType(let type): return "未知的 IPC 消息类型：\(type)"
        }
    }
}

final class MeyaFrameDecoder {
    private var buffer = Data()
    private(set) var discardedBytes = 0

    func append(_ data: Data) throws -> [MeyaFrame] {
        buffer.append(data)
        var frames: [MeyaFrame] = []
        let magic = Data("MEYA".utf8)
        while true {
            guard buffer.count >= magic.count else { break }
            if buffer.prefix(magic.count) != magic {
                if let marker = buffer.range(of: magic, options: [], in: 1..<buffer.count)?.lowerBound {
                    discardedBytes += marker
                    buffer.removeSubrange(0..<marker)
                } else {
                    let keep = min(magic.count - 1, buffer.count)
                    let drop = buffer.count - keep
                    if drop > 0 {
                        discardedBytes += drop
                        buffer.removeSubrange(0..<drop)
                    }
                    break
                }
            }
            guard buffer.count >= MeyaFrame.headerSize else { break }
            let version = buffer[4]
            guard version == MeyaFrame.version else {
                throw MeyaFrameError.unsupportedVersion(version)
            }
            guard let type = MeyaFrameType(rawValue: buffer[5]) else {
                throw MeyaFrameError.unknownType(buffer[5])
            }
            let flags: UInt16 = buffer.bigEndianValue(at: 6)
            let payloadSize: UInt32 = buffer.bigEndianValue(at: 8)
            guard payloadSize <= MeyaFrame.maximumPayload else {
                throw MeyaFrameError.payloadTooLarge
            }
            let total = MeyaFrame.headerSize + Int(payloadSize)
            guard buffer.count >= total else { break }
            let bytes = Array(buffer[12..<28])
            let session = UUID(uuid: (
                bytes[0], bytes[1], bytes[2], bytes[3],
                bytes[4], bytes[5], bytes[6], bytes[7],
                bytes[8], bytes[9], bytes[10], bytes[11],
                bytes[12], bytes[13], bytes[14], bytes[15]
            ))
            let sequence: UInt64 = buffer.bigEndianValue(at: 28)
            let payload = Data(buffer[MeyaFrame.headerSize..<total])
            frames.append(MeyaFrame(type: type, flags: flags, session: session, sequence: sequence, payload: payload))
            buffer.removeSubrange(0..<total)
        }
        return frames
    }
}

private extension Data {
    mutating func appendBigEndian<T: FixedWidthInteger>(_ value: T) {
        var bigEndian = value.bigEndian
        Swift.withUnsafeBytes(of: &bigEndian) { append(contentsOf: $0) }
    }

    func bigEndianValue<T: FixedWidthInteger>(at offset: Int) -> T {
        let size = MemoryLayout<T>.size
        return self[offset..<(offset + size)].reduce(T.zero) {
            ($0 << 8) | T($1)
        }
    }
}
