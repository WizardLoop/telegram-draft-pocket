import TelegramCore
import AccountContext

/// Identifies one visit to a composer scope. Returning to the same topic must not
/// make callbacks from the previous visit current again.
public struct ChatInputScopeTracker {
    public struct Token: Equatable {
        fileprivate let peerId: EnginePeer.Id?
        fileprivate let threadId: Int64?
        fileprivate let generation: UInt64
    }

    public private(set) var token: Token?

    public init() {
    }

    @discardableResult
    public mutating func update(peerId: EnginePeer.Id?, threadId: Int64?) -> Bool {
        if let token = self.token, token.peerId == peerId, token.threadId == threadId {
            return false
        }
        self.token = Token(peerId: peerId, threadId: threadId, generation: (self.token?.generation ?? 0) &+ 1)
        return true
    }

    public func matches(_ token: Token?) -> Bool {
        guard let token else { return false }
        return self.token == token
    }
}

extension ChatInterfaceState {
    /// Every field that a committed Pocket transfer replaces must agree in each
    /// participating window, including fields absent from the visible text.
    public func matchesDraftPocketComposer(_ captured: ChatInterfaceState, input: ChatTextInputState, effectiveInputLanguage: String?) -> Bool {
        return input == captured.composeInputState
            && self.replyMessageSubject == captured.replyMessageSubject
            && self.composeDisableUrlPreviews == captured.composeDisableUrlPreviews
            && self.sendMessageEffect == captured.sendMessageEffect
            && effectiveInputLanguage == captured.inputLanguage
    }
}

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
