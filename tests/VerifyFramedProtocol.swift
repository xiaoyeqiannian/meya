import Foundation

private struct FixtureFile: Decodable {
    let fixtures: [Fixture]
}

private struct Fixture: Decodable {
    let name: String
    let type: UInt8
    let flags: UInt16
    let session: String
    let sequence: UInt64
    let payloadUTF8: String?
    let payloadHex: String?
    let wireHex: String

    enum CodingKeys: String, CodingKey {
        case name, type, flags, session, sequence
        case payloadUTF8 = "payload_utf8"
        case payloadHex = "payload_hex"
        case wireHex = "wire_hex"
    }
}

private extension Data {
    init?(hex: String) {
        guard hex.count.isMultiple(of: 2) else { return nil }
        var output = Data(capacity: hex.count / 2)
        var index = hex.startIndex
        while index < hex.endIndex {
            let next = hex.index(index, offsetBy: 2)
            guard let byte = UInt8(hex[index..<next], radix: 16) else { return nil }
            output.append(byte)
            index = next
        }
        self = output
    }
}

@main
private enum VerifyFramedProtocol {
    static func main() throws {
        guard CommandLine.arguments.count == 2 else {
            throw NSError(domain: "MeyaFixture", code: 1)
        }
        let source = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
        let fixtureFile = try JSONDecoder().decode(FixtureFile.self, from: source)
        for fixture in fixtureFile.fixtures {
            guard let type = MeyaFrameType(rawValue: fixture.type),
                  let session = UUID(uuidString: fixture.session),
                  let expectedWire = Data(hex: fixture.wireHex)
            else { throw NSError(domain: "MeyaFixture", code: 2) }
            let payload = fixture.payloadUTF8.map { Data($0.utf8) }
                ?? fixture.payloadHex.flatMap(Data.init(hex:))
                ?? Data()
            let encoded = try MeyaFrame(
                type: type,
                flags: fixture.flags,
                session: session,
                sequence: fixture.sequence,
                payload: payload
            ).encoded()
            precondition(encoded == expectedWire, "encode mismatch: \(fixture.name)")
            let decoded = try MeyaFrameDecoder().append(expectedWire)
            precondition(decoded.count == 1, "decode count mismatch: \(fixture.name)")
            precondition(decoded[0].payload == payload, "payload mismatch: \(fixture.name)")
            precondition(decoded[0].session == session, "session mismatch: \(fixture.name)")
            precondition(decoded[0].sequence == fixture.sequence, "sequence mismatch: \(fixture.name)")
        }
        print("Swift IPC v2 golden fixtures passed")
    }
}
