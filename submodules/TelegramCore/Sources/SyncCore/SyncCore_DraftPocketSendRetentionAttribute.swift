import Postbox

/// Local-only routing metadata. No API serializer or network request encodes this attribute.
/// It carries the durable pre-send owner through asynchronous media transformation and retries.
public final class DraftPocketSendRetentionAttribute: MessageAttribute {
    public let operationId: String

    public init(operationId: String) {
        self.operationId = operationId
    }

    public init(decoder: PostboxDecoder) {
        self.operationId = decoder.decodeStringForKey("operationId", orElse: "")
    }

    public func encode(_ encoder: PostboxEncoder) {
        encoder.encodeString(self.operationId, forKey: "operationId")
    }
}
