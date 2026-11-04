import Foundation
import Postbox
import SwiftSignalKit

/// A product limit, unrelated to the Telegram API. No item is evicted at this limit.
public let draftPocketItemLimit = 20

public func draftPocketActiveStateToken(_ state: OpaqueChatInterfaceState) throws -> Data {
    return try AdaptedPostboxEncoder().encode(InternalChatInterfaceState(synchronizeableInputState: state.synchronizeableInputState, historyScrollMessageIndex: state.historyScrollMessageIndex, mediaDraftState: state.mediaDraftState, opaqueData: state.opaqueData))
}

/// The canonical entity codec stores ids only. This sidecar keeps the renderable file
/// references in both a pocket card and the active local draft after restoration.
public struct DraftPocketEmojiFiles: Codable {
    private let files: [Data]

    public init(content: ChatInputContent) {
        self.files = content.draftPocketCustomEmojiFiles().sorted(by: { $0.key < $1.key }).map {
            let encoder = PostboxEncoder()
            $0.value.encode(encoder)
            return encoder.makeData()
        }
    }

    public func resolving(_ content: ChatInputContent) -> ChatInputContent {
        var files: [Int64: TelegramMediaFile] = [:]
        for data in self.files {
            let file = TelegramMediaFile(decoder: PostboxDecoder(buffer: MemoryBuffer(data: data)))
            files[file.fileId.id] = file
        }
        return content.resolvingCustomEmojiFiles(files)
    }
}

/// Structural selection encoded with explicit fixed-width numbers for the Postbox codec.
public struct DraftPocketSelection: Codable {
    private struct Step: Codable {
        var block: Int32
        var slot: Int32
    }
    private struct Position: Codable {
        var path: [Step]
        var offset: Int32
        var affinity: Int32?

        private enum CodingKeys: String, CodingKey { case path, offset, affinity }

        init(_ position: ChatInputPosition) {
            self.path = position.path.map { Step(block: Int32(clamping: $0.blockIndex), slot: Int32(clamping: $0.slot)) }
            self.offset = Int32(clamping: position.offset)
            self.affinity = position.affinity?.rawValue
        }

        init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            self.path = try container.decode([Step].self, forKey: .path)
            self.offset = try container.decode(Int32.self, forKey: .offset)
            self.affinity = try container.decodeIfPresent(Int32.self, forKey: .affinity)
            if let affinity = self.affinity, ChatInputPositionAffinity(rawValue: affinity) == nil {
                throw DecodingError.dataCorruptedError(forKey: .affinity, in: container, debugDescription: "Unknown input selection affinity")
            }
        }

        var value: ChatInputPosition {
            return ChatInputPosition(path: self.path.map { ChatInputPathStep(blockIndex: Int($0.block), slot: Int($0.slot)) }, offset: Int(self.offset), affinity: self.affinity.flatMap(ChatInputPositionAffinity.init(rawValue:)))
        }
    }
    private var start: Position
    private var end: Position

    public init(_ selection: ChatInputSelection) {
        self.start = Position(selection.start)
        self.end = Position(selection.end)
    }

    public var value: ChatInputSelection {
        return ChatInputSelection(start: self.start.value, end: self.end.value)
    }
}

/// The preview is derived from `content`; it is never used as the restore payload.
/// The opaque state retains reply/quote, URL preview choices and input language.
public struct DraftPocketSnapshot: Codable {
    public let content: ChatInputContent
    private let storedSelection: DraftPocketSelection
    private let storedInterfaceState: Data
    private let emojiFiles: DraftPocketEmojiFiles

    private enum CodingKeys: String, CodingKey {
        case content, selection, interfaceState, emojiFiles
    }

    public var selection: ChatInputSelection { self.storedSelection.value }

    public init(content: ChatInputContent, selection: ChatInputSelection, state: OpaqueChatInterfaceState) throws {
        self.content = content
        self.storedSelection = DraftPocketSelection(selection)
        self.storedInterfaceState = try AdaptedPostboxEncoder().encode(InternalChatInterfaceState(
            synchronizeableInputState: state.synchronizeableInputState,
            historyScrollMessageIndex: state.historyScrollMessageIndex,
            mediaDraftState: state.mediaDraftState,
            opaqueData: state.opaqueData
        ))
        self.emojiFiles = DraftPocketEmojiFiles(content: content)
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let contentData = try container.decode(Data.self, forKey: .content)
        self.content = try AdaptedPostboxDecoder().decode(ChatInputContent.self, from: contentData)
        // ChatInputContent's general draft decoder intentionally drops unknown blocks.
        // A pocket transfer must fail closed instead of saving that shortened body.
        guard self.content.schemaVersion == 1, try AdaptedPostboxEncoder().encode(self.content) == contentData else {
            throw DraftPocketError.corruptStorage
        }
        self.storedSelection = try container.decode(DraftPocketSelection.self, forKey: .selection)
        self.storedInterfaceState = try container.decode(Data.self, forKey: .interfaceState)
        self.emojiFiles = try container.decode(DraftPocketEmojiFiles.self, forKey: .emojiFiles)
        _ = try AdaptedPostboxDecoder().decode(InternalChatInterfaceState.self, from: self.storedInterfaceState)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(AdaptedPostboxEncoder().encode(self.content), forKey: .content)
        try container.encode(self.storedSelection, forKey: .selection)
        try container.encode(self.storedInterfaceState, forKey: .interfaceState)
        try container.encode(self.emojiFiles, forKey: .emojiFiles)
    }

    public func interfaceState() throws -> OpaqueChatInterfaceState {
        let state = try AdaptedPostboxDecoder().decode(InternalChatInterfaceState.self, from: self.storedInterfaceState)
        return OpaqueChatInterfaceState(opaqueData: state.opaqueData, historyScrollMessageIndex: state.historyScrollMessageIndex, mediaDraftState: state.mediaDraftState, synchronizeableInputState: state.synchronizeableInputState)
    }

    public var resolvedContent: ChatInputContent {
        return self.emojiFiles.resolving(self.content)
    }

    fileprivate func resolvingCachedEmoji(transaction: Transaction) throws -> DraftPocketSnapshot {
        var files: [Int64: TelegramMediaFile] = [:]
        let content = self.resolvedContent
        for id in content.unresolvedCustomEmojiFileIds() {
            if let file = transaction.getMedia(MediaId(namespace: Namespaces.Media.CloudFile, id: id)) as? TelegramMediaFile {
                files[id] = file
            }
        }
        return try DraftPocketSnapshot(content: content.resolvingCustomEmojiFiles(files), selection: self.selection, state: self.interfaceState())
    }
}

public struct DraftPocketItem: Codable {
    public let id: String
    public let createdAt: Int32
    public let updatedAt: Int32
    public let peerId: Int64
    public let threadId: Int64?
    public let snapshot: DraftPocketSnapshot

    init(id: String, now: Int32, peerId: PeerId, threadId: Int64?, snapshot: DraftPocketSnapshot) {
        self.id = id
        self.createdAt = now
        self.updatedAt = now
        self.peerId = peerId.toInt64()
        self.threadId = threadId
        self.snapshot = snapshot
    }
}

/// Deleted items reserve their slot until Undo is committed or explicitly dismissed.
/// This keeps Undo reliable at the 20-card boundary without silently discarding a card.
struct DraftPocketState: Codable {
    var schemaVersion: Int32 = 1
    var revision: Int64 = 0
    var items: [DraftPocketItem] = []
    var deletedItems: [DraftPocketItem] = []
}

public struct DraftPocketView {
    public var pendingSends: [DraftPocketSendRecovery] = []
    public var mediaRecoveryUnavailable: Bool = false
    public let revision: Int64
    public let items: [DraftPocketItem]
    public let deletedItems: [DraftPocketItem]
    public let activeStateToken: Data?
    public let error: DraftPocketError?
}

public enum DraftPocketError: Error, Equatable {
    case conflict
    case full
    case itemNotFound
    case empty
    case unsupportedContext
    case mediaRetentionUnavailable
    case corruptStorage
    case encodingFailed
}

public enum DraftPocketOperation {
    case putAside(snapshot: DraftPocketSnapshot, clearedState: OpaqueChatInterfaceState)
    case restore(id: String, current: DraftPocketSnapshot?, restoredState: OpaqueChatInterfaceState)
    case delete(id: String)
    case undoDelete(id: String)
    case finalizeDelete(id: String)
}

public enum DraftPocketResult {
    case success(DraftPocketView)
    case failure(DraftPocketError)
}

private func draftPocketStoredActive(transaction: Transaction, peerId: PeerId, threadId: Int64?) -> StoredPeerChatInterfaceState? {
    if let threadId = threadId {
        return transaction.getPeerChatThreadInterfaceState(peerId, threadId: threadId)
    }
    return transaction.getPeerChatInterfaceState(peerId)
}

func draftPocketRead(transaction: Transaction, peerId: PeerId, threadId: Int64?) throws -> DraftPocketState {
    guard let entry = transaction.getPreferencesEntry(key: PreferencesKeys.draftPocket(peerId: peerId, threadId: threadId)) else {
        return DraftPocketState()
    }
    // Never turn a failed decode or a future schema into an empty writable pocket.
    let state = try AdaptedPostboxDecoder().decode(DraftPocketState.self, from: entry.data)
    guard state.schemaVersion == 1,
          state.revision >= 0,
          state.items.count + state.deletedItems.count <= draftPocketItemLimit,
          Set((state.items + state.deletedItems).map { $0.id }).count == state.items.count + state.deletedItems.count,
          (state.items + state.deletedItems).allSatisfy({ $0.peerId == peerId.toInt64() && $0.threadId == threadId }) else {
        throw DraftPocketError.corruptStorage
    }
    return state
}

func _internal_draftPocketView(transaction: Transaction, peerId: PeerId, threadId: Int64?) -> DraftPocketView {
    let token = draftPocketStoredActive(transaction: transaction, peerId: peerId, threadId: threadId)?.data
    do {
        let state = try draftPocketRead(transaction: transaction, peerId: peerId, threadId: threadId)
        var view = DraftPocketView(revision: state.revision, items: state.items, deletedItems: state.deletedItems, activeStateToken: token, error: nil)
        view.pendingSends = try DraftPocketMediaOwnership.read(transaction: transaction).pendingSends.filter { $0.peerId == peerId.toInt64() && $0.threadId == threadId }
        return view
    } catch {
        return DraftPocketView(revision: 0, items: [], deletedItems: [], activeStateToken: token, error: .corruptStorage)
    }
}

func _internal_draftPocket(postbox: Postbox, peerId: PeerId, threadId: Int64?) -> Signal<DraftPocketView, NoError> {
    return postbox.transaction { transaction in
        var view = _internal_draftPocketView(transaction: transaction, peerId: peerId, threadId: threadId)
        if !postbox.mediaBox.draftResourceRecoverySucceeded,
           let graph = try? DraftPocketMediaOwnership.read(transaction: transaction) {
            view.mediaRecoveryUnavailable = graph.entries.contains { entry in
                entry.owner.peerId == peerId.toInt64()
                    && (entry.owner.threadId == threadId || entry.owner.kind == 4)
                    && (postbox.mediaBox.draftResourceRecoveryIndexCorrupt
                        || entry.resources.contains { postbox.mediaBox.draftResourceRecoveryFailedIds.contains($0.id) })
            }
        }
        return view
    }
}

func _internal_observeDraftPocket(postbox: Postbox, peerId: PeerId, threadId: Int64?) -> Signal<DraftPocketView, NoError> {
    return postbox.preferencesView(keys: [PreferencesKeys.draftPocket(peerId: peerId, threadId: threadId), PreferencesKeys.draftPocketMediaOwnership])
    |> mapToSignal { _ in
        return _internal_draftPocket(postbox: postbox, peerId: peerId, threadId: threadId)
    }
}

/// All validation and encoding finishes before the first table mutation. Postbox serializes
/// this transaction, committing the pocket and active composer together. A repeated request
/// carrying an old revision/token fails without writing; it cannot create another card.
func _internal_updateDraftPocket(transaction: Transaction, peerId: PeerId, threadId: Int64?, expectedRevision: Int64, expectedActiveState: Data?, operation: DraftPocketOperation, now: Int32, itemId: String, mediaPreparation: DraftPocketMediaPreparation? = nil, encodePocket: (DraftPocketState) throws -> Data = { try AdaptedPostboxEncoder().encode($0) }) -> DraftPocketResult {
    guard peerId.namespace == Namespaces.Peer.CloudUser || peerId.namespace == Namespaces.Peer.CloudGroup || peerId.namespace == Namespaces.Peer.CloudChannel else {
        return .failure(.unsupportedContext)
    }
    let previousActive = draftPocketStoredActive(transaction: transaction, peerId: peerId, threadId: threadId)
    var state: DraftPocketState
    do {
        state = try draftPocketRead(transaction: transaction, peerId: peerId, threadId: threadId)
    } catch {
        return .failure(.corruptStorage)
    }
    guard state.revision == expectedRevision, state.revision < Int64.max else {
        return .failure(.conflict)
    }
    switch operation {
    case .putAside, .restore:
        guard previousActive?.data == expectedActiveState else { return .failure(.conflict) }
    case .delete, .undoDelete, .finalizeDelete:
        // Card-only edits never overwrite the active composer, so ordinary typing and
        // its autosave must not invalidate Undo or deletion of a different stored card.
        break
    }
    func mediaIsRetained(_ snapshot: DraftPocketSnapshot) -> Bool {
        if let mediaPreparation = mediaPreparation {
            guard let ids = draftPocketRetainedResourceIds(in: snapshot.resolvedContent) else { return false }
            return (try? mediaPreparation.resources(for: ids)) != nil
        }
        return snapshot.content.allMedia.isEmpty && snapshot.resolvedContent.draftPocketCustomEmojiFiles().values.allSatisfy({ !($0.resource is LocalFileMediaResource) && !($0.resource is LocalFileReferenceMediaResource) })
    }
    var active: OpaqueChatInterfaceState?
    var activeResourceIds: [String]?
    switch operation {
    case let .putAside(snapshot, clearedState):
        guard mediaIsRetained(snapshot) else { return .failure(.mediaRetentionUnavailable) }
        guard let snapshot = try? snapshot.resolvingCachedEmoji(transaction: transaction) else { return .failure(.encodingFailed) }
        guard let snapshotState = try? snapshot.interfaceState() else { return .failure(.encodingFailed) }
        guard snapshotState.mediaDraftState == nil, clearedState.mediaDraftState == nil else { return .failure(.unsupportedContext) }
        let reply = snapshotState.synchronizeableInputState?.replySubject
        guard !snapshot.content.isEmpty || reply != nil else { return .failure(.empty) }
        guard state.items.count + state.deletedItems.count < draftPocketItemLimit else { return .failure(.full) }
        state.items.insert(DraftPocketItem(id: itemId, now: now, peerId: peerId, threadId: threadId, snapshot: snapshot), at: 0)
        active = clearedState
        activeResourceIds = []
    case let .restore(id, current, restoredState):
        guard let index = state.items.firstIndex(where: { $0.id == id }) else { return .failure(.itemNotFound) }
        guard mediaIsRetained(state.items[index].snapshot), current.map(mediaIsRetained) ?? true else { return .failure(.mediaRetentionUnavailable) }
        guard restoredState.mediaDraftState == nil else { return .failure(.unsupportedContext) }
        activeResourceIds = draftPocketRetainedResourceIds(in: state.items[index].snapshot.resolvedContent)
        state.items.remove(at: index)
        if let current = current {
            guard let current = try? current.resolvingCachedEmoji(transaction: transaction) else { return .failure(.encodingFailed) }
            guard (try? current.interfaceState())?.mediaDraftState == nil else { return .failure(.unsupportedContext) }
            state.items.insert(DraftPocketItem(id: itemId, now: now, peerId: peerId, threadId: threadId, snapshot: current), at: 0)
        }
        active = restoredState
    case let .delete(id):
        guard let index = state.items.firstIndex(where: { $0.id == id }) else { return .failure(.itemNotFound) }
        state.deletedItems.append(state.items.remove(at: index))
    case let .undoDelete(id):
        guard let index = state.deletedItems.firstIndex(where: { $0.id == id }) else { return .failure(.itemNotFound) }
        state.items.insert(state.deletedItems.remove(at: index), at: 0)
    case let .finalizeDelete(id):
        guard let index = state.deletedItems.firstIndex(where: { $0.id == id }) else { return .failure(.itemNotFound) }
        state.deletedItems.remove(at: index)
    }
    state.revision += 1

    let pocketData: Data
    let activeData: Data?
    let ownershipData: Data?
    do {
        if let preparation = mediaPreparation {
            var ownership = try DraftPocketMediaOwnership.read(transaction: transaction)
            ownership.entries.removeAll { $0.owner.peerId == peerId.toInt64() && $0.owner.threadId == threadId && ($0.owner.kind == 0 || $0.owner.kind == 1) }
            for (items, deleted) in [(state.items, false), (state.deletedItems, true)] {
                for item in items {
                    guard let ids = draftPocketRetainedResourceIds(in: item.snapshot.resolvedContent) else { throw DraftPocketError.mediaRetentionUnavailable }
                    try ownership.set(.card(peerId: peerId, threadId: threadId, id: item.id, deleted: deleted), resources: preparation.resources(for: ids))
                }
            }
            if let ids = activeResourceIds {
                try ownership.set(.active(peerId: peerId, threadId: threadId), resources: preparation.resources(for: ids))
            }
            ownershipData = try ownership.encodedForCommit()
        } else {
            ownershipData = nil
        }
        pocketData = try encodePocket(state)
        // Verify the actual codec before a destructive transfer, including unknown block loss.
        let decoded = try AdaptedPostboxDecoder().decode(DraftPocketState.self, from: pocketData)
        guard decoded.items.count == state.items.count,
              zip(decoded.items, state.items).allSatisfy({ pair in pair.0.snapshot.content == pair.1.snapshot.content && pair.0.snapshot.selection == pair.1.snapshot.selection }) else {
            return .failure(.encodingFailed)
        }
        if let active = active {
            activeData = try AdaptedPostboxEncoder().encode(InternalChatInterfaceState(synchronizeableInputState: active.synchronizeableInputState, historyScrollMessageIndex: active.historyScrollMessageIndex, mediaDraftState: active.mediaDraftState, opaqueData: active.opaqueData))
            _ = try AdaptedPostboxDecoder().decode(InternalChatInterfaceState.self, from: activeData!)
        } else {
            activeData = nil
        }
    } catch {
        return .failure(.encodingFailed)
    }
    if let ownershipData = ownershipData { DraftPocketMediaOwnership.commitEncoded(ownershipData, transaction: transaction) }
    transaction.setPreferencesEntry(key: PreferencesKeys.draftPocket(peerId: peerId, threadId: threadId), value: PreferencesEntry(data: pocketData))
    if let active = active, let activeData = activeData {
        let stored = StoredPeerChatInterfaceState(overrideChatTimestamp: active.synchronizeableInputState?.timestamp, historyScrollMessageIndex: active.historyScrollMessageIndex, associatedMessageIds: active.synchronizeableInputState?.replySubject.map { [$0.messageId] } ?? [], data: activeData)
        if let threadId = threadId {
            transaction.setPeerChatThreadInterfaceState(peerId, threadId: threadId, state: stored)
        } else {
            transaction.setPeerChatInterfaceState(peerId, state: stored)
        }
        let previousSync = previousActive?.data.flatMap { try? AdaptedPostboxDecoder().decode(InternalChatInterfaceState.self, from: $0).synchronizeableInputState }
        if previousSync != active.synchronizeableInputState {
            addSynchronizeChatInputStateOperation(transaction: transaction, peerId: peerId, threadId: threadId)
        }
    }
    return .success(_internal_draftPocketView(transaction: transaction, peerId: peerId, threadId: threadId))
}

func _internal_updateDraftPocket(postbox: Postbox, peerId: PeerId, threadId: Int64?, expectedRevision: Int64, expectedActiveState: Data?, operation: DraftPocketOperation) -> Signal<DraftPocketResult, NoError> {
    return postbox.transaction { transaction in
        return _internal_updateDraftPocket(transaction: transaction, peerId: peerId, threadId: threadId, expectedRevision: expectedRevision, expectedActiveState: expectedActiveState, operation: operation, now: Int32(clamping: Int64(Date().timeIntervalSince1970)), itemId: UUID().uuidString)
    }
}
