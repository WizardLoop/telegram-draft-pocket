import Foundation
import Postbox

// Shared by the engine facade and native transaction tests. A corrupt optional ownership
// graph must preserve its pins without blocking ordinary canonical composer autosave.
func _internal_setOpaqueChatInterfaceState(transaction: Transaction, peerId: PeerId, threadId: Int64?, state: OpaqueChatInterfaceState, retainedDraftResourceIds: [String]?) -> Bool {
    guard let data = try? AdaptedPostboxEncoder().encode(InternalChatInterfaceState(
        synchronizeableInputState: state.synchronizeableInputState,
        historyScrollMessageIndex: state.historyScrollMessageIndex,
        mediaDraftState: state.mediaDraftState,
        opaqueData: state.opaqueData
    )) else {
        return false
    }

    let ownershipData: Data?
    do {
        if let ids = retainedDraftResourceIds {
            var graph = try DraftPocketMediaOwnership.read(transaction: transaction)
            let owner = DraftPocketMediaOwner.active(peerId: peerId, threadId: threadId)
            if let existing = graph.entries.first(where: { $0.owner == owner }) {
                let remaining = existing.resources.filter { ids.contains($0.id) }
                if remaining != existing.resources {
                    try graph.set(owner, resources: remaining)
                    ownershipData = try graph.encodedForCommit()
                } else { ownershipData = nil }
            } else { ownershipData = nil }
        } else { ownershipData = nil }
    } catch { ownershipData = nil }
    if let data = ownershipData { DraftPocketMediaOwnership.commitEncoded(data, transaction: transaction) }
    #if DEBUG
    let _ = try! AdaptedPostboxDecoder().decode(InternalChatInterfaceState.self, from: data)
    #endif

    var overrideChatTimestamp: Int32?
    if let inputState = state.synchronizeableInputState {
        overrideChatTimestamp = inputState.timestamp
    }

    if let mediaDraftState = state.mediaDraftState {
        if let current = overrideChatTimestamp, mediaDraftState.timestamp < current {
        } else {
            overrideChatTimestamp = mediaDraftState.timestamp
        }
    }

    let storedState = StoredPeerChatInterfaceState(
        overrideChatTimestamp: overrideChatTimestamp,
        historyScrollMessageIndex: state.historyScrollMessageIndex,
        associatedMessageIds: (state.synchronizeableInputState?.replySubject?.messageId).flatMap({ [$0] }) ?? [],
        data: data
    )

    if let threadId = threadId {
        var currentInputState: SynchronizeableChatInputState?
        if let peerChatInterfaceState = transaction.getPeerChatThreadInterfaceState(peerId, threadId: threadId), let data = peerChatInterfaceState.data {
            currentInputState = (try? AdaptedPostboxDecoder().decode(InternalChatInterfaceState.self, from: data))?.synchronizeableInputState
        }
        let updatedInputState = state.synchronizeableInputState

        if currentInputState != updatedInputState {
            if peerId.namespace == Namespaces.Peer.CloudUser || peerId.namespace == Namespaces.Peer.CloudChannel || peerId.namespace == Namespaces.Peer.CloudGroup {
                addSynchronizeChatInputStateOperation(transaction: transaction, peerId: peerId, threadId: threadId)
            }
        }
        transaction.setPeerChatThreadInterfaceState(peerId, threadId: threadId, state: storedState)
    } else {
        var currentInputState: SynchronizeableChatInputState?
        if let peerChatInterfaceState = transaction.getPeerChatInterfaceState(peerId), let data = peerChatInterfaceState.data {
            currentInputState = (try? AdaptedPostboxDecoder().decode(InternalChatInterfaceState.self, from: data))?.synchronizeableInputState
        }
        let updatedInputState = state.synchronizeableInputState

        if currentInputState != updatedInputState {
            if peerId.namespace == Namespaces.Peer.CloudUser || peerId.namespace == Namespaces.Peer.CloudChannel || peerId.namespace == Namespaces.Peer.CloudGroup {
                addSynchronizeChatInputStateOperation(transaction: transaction, peerId: peerId, threadId: nil)
            }
        }
        transaction.setPeerChatInterfaceState(
            peerId,
            state: storedState
        )
    }
    return ownershipData != nil
}
