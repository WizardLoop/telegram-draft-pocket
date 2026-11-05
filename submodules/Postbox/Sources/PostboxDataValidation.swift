import Foundation

/// Opt-in framing validation for persisted records whose failure must be recoverable.
/// This does not invoke registered object decoders or interpret opaque Bytes payloads.
/// Callers that decode another Postbox record from Bytes must validate that record too.
public extension PostboxDecoder {
    static func isWellFormedData(_ data: Data) -> Bool {
        return PostboxDataValidation.validate(data)
    }
}

enum PostboxDataValidation {
    // Limits apply only to opt-in callers, never to ordinary Postbox decoding.
    static let maximumBytes = 64 * 1024 * 1024
    static let maximumDepth = 128
    static let maximumValues = 1_000_000

    static func validate(_ data: Data) -> Bool {
        guard data.count <= self.maximumBytes else { return false }
        return data.withUnsafeBytes { bytes in
            var reader = Reader(bytes: bytes)
            return reader.object(end: bytes.count, depth: 0) && reader.offset == bytes.count
        }
    }

    private struct Reader {
        let bytes: UnsafeRawBufferPointer
        var offset = 0
        var remainingValues = PostboxDataValidation.maximumValues

        mutating func consume(_ count: Int, end: Int) -> Bool {
            guard count >= 0, self.offset <= end, count <= end - self.offset else { return false }
            self.offset += count
            return true
        }

        mutating func length(end: Int) -> Int? {
            guard self.offset <= end, end - self.offset >= 4 else { return nil }
            // The encoder writes little-endian Int32 on supported Apple platforms. Avoid
            // unaligned pointer loads, negative counts and unchecked count multiplication.
            var value: UInt32 = 0
            for i in 0 ..< 4 {
                value |= UInt32(self.bytes[self.offset + i]) << (i * 8)
            }
            self.offset += 4
            guard value <= UInt32(Int32.max) else { return nil }
            return Int(value)
        }

        mutating func object(end: Int, depth: Int) -> Bool {
            guard depth <= PostboxDataValidation.maximumDepth else { return false }
            while self.offset < end {
                guard self.remainingValues > 0 else { return false }
                self.remainingValues -= 1
                let keyLength = Int(self.bytes[self.offset])
                self.offset += 1
                guard keyLength <= Int(Int8.max), self.consume(keyLength, end: end),
                      self.offset < end,
                      let type = ValueType(rawValue: Int8(bitPattern: self.bytes[self.offset])) else { return false }
                self.offset += 1
                guard self.value(type, end: end, depth: depth) else { return false }
            }
            return self.offset == end
        }

        mutating func nestedObject(end: Int, depth: Int) -> Bool {
            guard self.consume(4, end: end), let count = self.length(end: end),
                  count <= end - self.offset else { return false }
            return self.object(end: self.offset + count, depth: depth + 1)
        }

        mutating func value(_ type: ValueType, end: Int, depth: Int) -> Bool {
            switch type {
            case .Int32: return self.consume(4, end: end)
            case .Int64, .Double: return self.consume(8, end: end)
            case .Bool: return self.consume(1, end: end)
            case .Nil: return true
            case .String, .Bytes:
                guard let count = self.length(end: end) else { return false }
                return self.consume(count, end: end)
            case .Object:
                return self.nestedObject(end: end, depth: depth)
            case .Int32Array, .Int64Array:
                guard let count = self.length(end: end) else { return false }
                let width = type == .Int32Array ? 4 : 8
                guard count <= (end - self.offset) / width, count <= self.remainingValues else { return false }
                self.remainingValues -= count
                return self.consume(count * width, end: end)
            case .ObjectArray, .ObjectDictionary:
                guard let count = self.length(end: end) else { return false }
                let width = type == .ObjectDictionary ? 2 : 1
                guard count <= (end - self.offset) / (8 * width),
                      count <= self.remainingValues / width else { return false }
                self.remainingValues -= count * width
                for _ in 0 ..< count {
                    for _ in 0 ..< width {
                        guard self.nestedObject(end: end, depth: depth) else { return false }
                    }
                }
                return true
            case .StringArray, .BytesArray:
                guard let count = self.length(end: end), count <= (end - self.offset) / 4,
                      count <= self.remainingValues else { return false }
                self.remainingValues -= count
                for _ in 0 ..< count {
                    guard let size = self.length(end: end), self.consume(size, end: end) else { return false }
                }
                return true
            }
        }
    }
}
