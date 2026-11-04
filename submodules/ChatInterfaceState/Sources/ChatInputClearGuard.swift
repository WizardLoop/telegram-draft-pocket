import TelegramCore
import AccountContext

/// A delayed send callback may clear only the composer it captured. Pocket transfers
/// advance generation even when the replacement draft happens to have identical content.
public struct ChatInputClearGuard {
    private let peerId: EnginePeer.Id?
    private var threadId: Int64?
    private let generation: UInt64
    private let input: ChatTextInputState
    private let state: ChatInterfaceState

    public init(peerId: EnginePeer.Id?, threadId: Int64?, generation: UInt64, input: ChatTextInputState, state: ChatInterfaceState) {
        self.peerId = peerId
        self.threadId = threadId
        self.generation = generation
        self.input = input
        self.state = state
    }

    /// Explicit routing to a forum topic moves the same draft; no other scope is rebased.
    public func withUpdatedThreadId(_ threadId: Int64?) -> ChatInputClearGuard {
        var result = self
        result.threadId = threadId
        return result
    }

    public func matches(peerId: EnginePeer.Id?, threadId: Int64?, generation: UInt64, input: ChatTextInputState, state: ChatInterfaceState) -> Bool {
        return self.peerId == peerId && self.threadId == threadId && self.generation == generation
            && self.input == input
            && self.state.replyMessageSubject == state.replyMessageSubject
            && self.state.sendMessageEffect == state.sendMessageEffect
            && self.state.postSuggestionState == state.postSuggestionState
            && self.state.composeDisableUrlPreviews == state.composeDisableUrlPreviews
            && self.state.forwardMessageIds == state.forwardMessageIds
            && self.state.forwardOptionsState == state.forwardOptionsState
            && self.state.editMessage == state.editMessage
            && self.state.mediaDraftState == state.mediaDraftState
            && self.state.silentPosting == state.silentPosting
    }
}
