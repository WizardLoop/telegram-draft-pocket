import Foundation
import Postbox
import SwiftSignalKit

/// Account-local ownership. No StorageBox message references are fabricated for drafts.
/// The filesystem pin set is a conservative superset of this committed graph.
struct DraftPocketMediaOwner: Codable, Hashable {
    // Fixed-width fields are intentional: the real Postbox Codable adapter rejects Swift Int.
    let kind: Int32 // 0 card, 1 deleted card, 2 active composer, 3 pending MessageId, 4 pending global id, 5 pre-send
    let peerId: Int64
    let threadId: Int64?
    let itemId: String?
    let messageNamespace: Int32?
    let messageId: Int32?
    var globallyUniqueId: Int64? = nil

    static func card(peerId: PeerId, threadId: Int64?, id: String, deleted: Bool) -> Self {
        return Self(kind: deleted ? 1 : 0, peerId: peerId.toInt64(), threadId: threadId, itemId: id, messageNamespace: nil, messageId: nil)
    }
    static func active(peerId: PeerId, threadId: Int64?) -> Self {
        return Self(kind: 2, peerId: peerId.toInt64(), threadId: threadId, itemId: nil, messageNamespace: nil, messageId: nil)
    }
    static func pending(_ id: MessageId) -> Self {
        return Self(kind: 3, peerId: id.peerId.toInt64(), threadId: nil, itemId: nil, messageNamespace: id.namespace, messageId: id.id)
    }
    static func pending(peerId: PeerId, globallyUniqueId: Int64, operationId: String) -> Self {
        return Self(kind: 4, peerId: peerId.toInt64(), threadId: nil, itemId: operationId, messageNamespace: nil, messageId: nil, globallyUniqueId: globallyUniqueId)
    }
    static func sending(peerId: PeerId, threadId: Int64?, operationId: String) -> Self {
        return Self(kind: 5, peerId: peerId.toInt64(), threadId: threadId, itemId: operationId, messageNamespace: nil, messageId: nil)
    }
    var pendingMessageId: MessageId? {
        guard self.kind == 3, let namespace = self.messageNamespace, let id = self.messageId else { return nil }
        return MessageId(peerId: PeerId(self.peerId), namespace: namespace, id: id)
    }
    var isValid: Bool {
        switch self.kind {
        case 0, 1: return !(self.itemId?.isEmpty ?? true) && self.messageNamespace == nil && self.messageId == nil
        case 2: return self.itemId == nil && self.messageNamespace == nil && self.messageId == nil
        case 3: return self.itemId == nil && self.threadId == nil && self.pendingMessageId != nil
        case 4: return !(self.itemId?.isEmpty ?? true) && self.threadId == nil && self.messageId == nil && self.messageNamespace == nil && self.globallyUniqueId != nil
        case 5: return !(self.itemId?.isEmpty ?? true) && self.messageNamespace == nil && self.messageId == nil && self.globallyUniqueId == nil
        default: return false
        }
    }
}

struct DraftPocketMediaOwnership: Codable {
    struct Entry: Codable {
        var owner: DraftPocketMediaOwner
        var resources: [MediaBoxResourceRetention.Resource]
    }
    var version: Int32 = 1
    var revision: Int64 = 0
    var entries: [Entry] = []
    var pendingSends: [DraftPocketSendRecovery] = []

    var allResources: [MediaBoxResourceRetention.Resource] {
        return self.entries.flatMap { $0.resources }
    }

    static func read(transaction: Transaction) throws -> Self {
        guard let entry = transaction.getPreferencesEntry(key: PreferencesKeys.draftPocketMediaOwnership) else { return Self() }
        let value = try AdaptedPostboxDecoder(validateData: true).decode(Self.self, from: entry.data)
        guard value.version == 1, value.revision >= 0,
              value.entries.allSatisfy({ $0.owner.isValid && !$0.resources.isEmpty }),
              Set(value.entries.map { $0.owner }).count == value.entries.count else { throw DraftPocketError.corruptStorage }
        guard value.pendingSends.count <= draftPocketItemLimit,
              Set(value.pendingSends.map { $0.id }).count == value.pendingSends.count,
              value.pendingSends.allSatisfy({ recovery in value.entries.contains { $0.owner == .sending(peerId: PeerId(recovery.peerId), threadId: recovery.threadId, operationId: recovery.id) } }) else { throw DraftPocketError.corruptStorage }
        var resources: [String: MediaBoxResourceRetention.Resource] = [:]
        for resource in value.allResources {
            if let previous = resources[resource.id], previous != resource { throw DraftPocketError.corruptStorage }
            resources[resource.id] = resource
        }
        return value
    }

    mutating func set(_ owner: DraftPocketMediaOwner, resources: [MediaBoxResourceRetention.Resource]) throws {
        guard owner.isValid else { throw DraftPocketError.corruptStorage }
        self.entries.removeAll(where: { $0.owner == owner })
        if !resources.isEmpty {
            self.entries.append(Entry(owner: owner, resources: resources.sorted(by: { $0.id < $1.id })))
        }
    }

    /// Encoding must finish before ANY pocket/active/message table mutation in the transaction.
    func encodedForCommit() throws -> Data {
        guard self.revision < Int64.max else { throw DraftPocketError.corruptStorage }
        var updated = self
        updated.revision += 1
        let data = try AdaptedPostboxEncoder().encode(updated)
        // A prepared send may authorize clearing the composer only after its recovery
        // record can be read by the same bounded decoder used after a restart. This
        // also validates Snapshot's separately encoded content/interface-state bytes.
        _ = try AdaptedPostboxDecoder(validateData: true).decode(Self.self, from: data)
        return data
    }

    static func commitEncoded(_ data: Data, transaction: Transaction) {
        transaction.setPreferencesEntry(key: PreferencesKeys.draftPocketMediaOwnership, value: PreferencesEntry(data: data))
    }
}

/// Preparation and collection use lock -> fresh Postbox transaction ordering. Callers must
/// retain this object in the transaction RESULT, not only in its closure, until SQLite commits.
/// Synchronous MediaBox move operations use a nonblocking lock and retain their source on contention.
final class DraftPocketMediaPreparation {
    let session: MediaBoxResourceRetention.Session
    let resources: [String: MediaBoxResourceRetention.Resource]

    init(mediaBox: MediaBox, sources: [MediaResourceId: String]) throws {
        self.session = try mediaBox.draftResourceRetention.beginSession()
        var resources: [String: MediaBoxResourceRetention.Resource] = [:]
        for resource in try self.session.pinnedResources() { resources[resource.id] = resource }
        for (id, source) in sources.sorted(by: { $0.key.stringRepresentation < $1.key.stringRepresentation }) {
            resources[id.stringRepresentation] = try self.session.stage(id: id, completeSourcePath: source)
        }
        self.resources = resources
    }

    func resources(for ids: [String]) throws -> [MediaBoxResourceRetention.Resource] {
        return try Set(ids).sorted().map { id in
            guard let resource = self.resources[id] else { throw DraftPocketError.mediaRetentionUnavailable }
            return resource
        }
    }
}

/// Preparation can publish several pins before a later source fails. Run independently of the
/// cancelled observer, after the failed/cancelled lease releases; the collector takes a fresh
/// locked ownership snapshot, so a concurrently committed card/send remains protected.
func draftPocketFinishPreparationFailure(postbox: Postbox, completion: @escaping () -> Void) {
    guard postbox.mediaBox.draftResourceRetention.hasPersistedResources else {
        completion()
        return
    }
    let _ = _internal_collectDraftPocketMedia(postbox: postbox).startStandalone(completed: completion)
}

func draftPocketSchedulePreparationCollection(postbox: Postbox) {
    draftPocketFinishPreparationFailure(postbox: postbox, completion: {})
}

/// Resource discovery uses the canonical media objects, including nested blocks and emoji files.
/// Unknown/unfinished media fail before mutation; preparation never starts a fetch or an upload.
func draftPocketMediaResources(_ content: ChatInputContent) throws -> [MediaResource] {
    var media = content.allMedia.map { $0._asMedia() }
    for file in content.draftPocketCustomEmojiFiles().values {
        if file.resource is LocalFileMediaResource || file.resource is LocalFileReferenceMediaResource { media.append(file) }
    }
    return try draftPocketMediaResources(media: media)
}

func draftPocketMediaResources(media: [Media]) throws -> [MediaResource] {
    var result: [MediaResource] = []
    func append(_ media: Media) throws {
        if let file = media as? TelegramMediaFile {
            result.append(file.resource)
            result.append(contentsOf: file.previewRepresentations.map { $0.resource })
            result.append(contentsOf: file.videoThumbnails.map { $0.resource })
            if let cover = file.videoCover { try append(cover) }
            for alternative in file.alternativeRepresentations { try append(alternative) }
        } else if let image = media as? TelegramMediaImage {
            result.append(contentsOf: image.representations.map { $0.resource })
            result.append(contentsOf: image.videoRepresentations.map { $0.resource })
            if let video = image.video { try append(video) }
        } else {
            throw DraftPocketError.mediaRetentionUnavailable
        }
    }
    for item in media { try append(item) }
    return result
}

func draftPocketRichAttributeMedia(_ attributes: [MessageAttribute]) -> [Media] {
    return attributes.compactMap { $0 as? RichTextMessageAttribute }.flatMap { attribute in
        return Array(attribute.instantPage.media.values).map { $0._asMedia() } + (attribute.fullInstantPage.map { Array($0.media.values).map { $0._asMedia() } } ?? [])
    }
}

/// Nil means an unsupported representation, so callers must preserve existing ownership.
public func draftPocketRetainedResourceIds(in content: ChatInputContent) -> [String]? {
    guard let resources = try? draftPocketMediaResources(content) else { return nil }
    return Array(Set(resources.map { $0.id.stringRepresentation })).sorted()
}

func _internal_updateDraftPocketRetainingMedia(postbox: Postbox, peerId: PeerId, threadId: Int64?, expectedRevision: Int64, expectedActiveState: Data?, operation: DraftPocketOperation) -> Signal<DraftPocketResult, NoError> {
    return _internal_draftPocket(postbox: postbox, peerId: peerId, threadId: threadId)
    |> mapToSignal { view -> Signal<DraftPocketResult, NoError> in
        return Signal { subscriber in
            let disposable = MetaDisposable()
            Queue.concurrentBackgroundQueue().async {
                do {
                    var contents: [ChatInputContent] = []
                    switch operation {
                    case let .putAside(snapshot, _): contents.append(snapshot.resolvedContent)
                    case let .restore(id, current, _):
                        if let current = current { contents.append(current.resolvedContent) }
                        guard let target = view.items.first(where: { $0.id == id }) else { throw DraftPocketError.itemNotFound }
                        contents.append(target.snapshot.resolvedContent)
                    case .delete, .undoDelete, .finalizeDelete: break
                    }
                    var sources: [MediaResourceId: String] = [:]
                    for content in contents {
                        for resource in try draftPocketMediaResources(content) {
                            if let resource = resource as? LocalFileReferenceMediaResource {
                                sources[resource.id] = resource.localFilePath
                            } else {
                                sources[resource.id] = postbox.mediaBox.resourcePath(resource)
                            }
                        }
                    }
                    let preparation = try DraftPocketMediaPreparation(mediaBox: postbox.mediaBox, sources: sources)
                    disposable.set(postbox.transaction { transaction -> (DraftPocketResult, DraftPocketMediaPreparation) in
                        // A disposed next observer cannot perform cleanup after a CAS rejection.
                        // Queue it from the transaction itself: it waits for the result-held lease
                        // to release after commit, then reads a fresh ownership snapshot.
                        defer { draftPocketSchedulePreparationCollection(postbox: postbox) }
                        let result = _internal_updateDraftPocket(transaction: transaction, peerId: peerId, threadId: threadId, expectedRevision: expectedRevision, expectedActiveState: expectedActiveState, operation: operation, now: Int32(clamping: Int64(Date().timeIntervalSince1970)), itemId: UUID().uuidString, mediaPreparation: preparation)
                        return (result, preparation)
                    }.start(next: { result, preparation in
                        // Subscriber callbacks may synchronously use MediaBox. Release first.
                        preparation.session.finish()
                        subscriber.putNext(result)
                        subscriber.putCompletion()
                        let _ = _internal_collectDraftPocketMedia(postbox: postbox).startStandalone()
                    }))
                } catch let error as DraftPocketError {
                    draftPocketFinishPreparationFailure(postbox: postbox) {
                        subscriber.putNext(.failure(error))
                        subscriber.putCompletion()
                    }
                } catch {
                    draftPocketFinishPreparationFailure(postbox: postbox) {
                        subscriber.putNext(.failure(.mediaRetentionUnavailable))
                        subscriber.putCompletion()
                    }
                }
            }
            return ActionDisposable {
                disposable.dispose()
                draftPocketSchedulePreparationCollection(postbox: postbox)
            }
        }
    }
}

/// Only sends from a scope with an owned restored draft enter this path. The returned lease
/// protects both original source IDs and transformed resource IDs until enqueue commits.
func _internal_prepareDraftPocketOutgoingMedia(postbox: Postbox, peerId: PeerId, messages: [EnqueueMessage]) -> Signal<Result<DraftPocketMediaPreparation?, Error>, NoError> {
    let operationIds = Set(messages.flatMap { message -> [MessageAttribute] in
        switch message {
        case let .message(_, attributes, _, _, _, _, _, _, _, _): return attributes
        case let .forward(_, _, _, attributes, _): return attributes
        }
    }.compactMap { ($0 as? DraftPocketSendRetentionAttribute)?.operationId })
    if operationIds.isEmpty { return .single(.success(nil)) }
    return postbox.transaction { transaction -> Result<Bool, Error> in
        do {
            let graph = try DraftPocketMediaOwnership.read(transaction: transaction)
            guard operationIds.allSatisfy({ id in graph.pendingSends.contains { $0.id == id && $0.peerId == peerId.toInt64() } }) else { throw DraftPocketError.conflict }
            return .success(true)
        } catch { return .failure(error) }
    }
    |> mapToSignal { needsPreparation in
        return Signal { subscriber in
            Queue.concurrentBackgroundQueue().async {
                do {
                    guard try needsPreparation.get() else {
                        subscriber.putNext(.success(nil))
                        subscriber.putCompletion()
                        return
                    }
                    var media: [Media] = []
                    for message in messages {
                        switch message {
                        case let .message(_, attributes, inlineStickers, reference, _, _, _, _, _, _):
                            media.append(contentsOf: draftPocketRichAttributeMedia(attributes))
                            if let reference = reference { media.append(reference.media) }
                            for file in inlineStickers.values {
                                if let file = file as? TelegramMediaFile, file.resource is LocalFileMediaResource || file.resource is LocalFileReferenceMediaResource { media.append(file) }
                            }
                        case .forward: break
                        }
                    }
                    // Transforms may enqueue async MediaBox writes. Flush before taking the lock;
                    // waiting for the data queue while holding it can deadlock against cleanup.
                    postbox.mediaBox.dataQueue.sync {}
                    var sources: [MediaResourceId: String] = [:]
                    for resource in try draftPocketMediaResources(media: media) {
                        sources[resource.id] = (resource as? LocalFileReferenceMediaResource)?.localFilePath ?? postbox.mediaBox.resourcePath(resource)
                    }
                    subscriber.putNext(.success(try DraftPocketMediaPreparation(mediaBox: postbox.mediaBox, sources: sources)))
                    subscriber.putCompletion()
                    // Also covers disposal before the background prepare began: its result may
                    // be ignored without ever subscribing to an enqueue transaction.
                    draftPocketSchedulePreparationCollection(postbox: postbox)
                } catch {
                    draftPocketFinishPreparationFailure(postbox: postbox) {
                        subscriber.putNext(.failure(error))
                        subscriber.putCompletion()
                    }
                }
            }
            return ActionDisposable { draftPocketSchedulePreparationCollection(postbox: postbox) }
        }
    }
}

/// Encode the handoff before addMessages. The global ID is allocated by the existing enqueue
/// pipeline, so no speculative MessageId or fake StorageBox reference is necessary.
func draftPocketPendingOwnershipData(transaction: Transaction, peerId: PeerId, messages: [StoreMessage], preparation: DraftPocketMediaPreparation?, retryingMessageIds: Set<MessageId> = []) throws -> Data? {
    guard messages.contains(where: { $0.attributes.contains(where: { $0 is DraftPocketSendRetentionAttribute }) }) else { return nil }
    var graph = try DraftPocketMediaOwnership.read(transaction: transaction)
    var consumed = Set<String>()
    for message in messages {
        let markers = message.attributes.compactMap { $0 as? DraftPocketSendRetentionAttribute }
        guard !markers.isEmpty else { continue }
        guard markers.count == 1, let marker = markers.first, let globalId = message.globallyUniqueId else { throw DraftPocketError.corruptStorage }
        let source: [MediaBoxResourceRetention.Resource]
        if let recovery = graph.pendingSends.first(where: { $0.id == marker.operationId && $0.peerId == peerId.toInt64() }) {
            let owner = DraftPocketMediaOwner.sending(peerId: peerId, threadId: recovery.threadId, operationId: recovery.id)
            guard let entry = graph.entries.first(where: { $0.owner == owner }) else { throw DraftPocketError.corruptStorage }
            source = entry.resources
        } else {
            // Retry transfers ownership from still-existing failed local messages in the same
            // transaction. It cannot resurrect an already sent/recovered operation.
            let prior = graph.entries.filter { entry in
                guard entry.owner.kind == 4, entry.owner.itemId == marker.operationId, let id = entry.owner.globallyUniqueId,
                      let messageId = transaction.messageIdForGloballyUniqueMessageId(peerId: peerId, id: id) else { return false }
                return retryingMessageIds.contains(messageId) && Namespaces.Message.allLocal.contains(messageId.namespace) && transaction.getMessage(messageId) != nil
            }
            guard !prior.isEmpty else { throw DraftPocketError.conflict }
            source = prior.flatMap { $0.resources }
        }
        let actualIds = try draftPocketMediaResources(media: message.media + draftPocketRichAttributeMedia(message.attributes)).map { $0.id.stringRepresentation }
        let ids = Set(source.map { $0.id } + actualIds)
        let resources: [MediaBoxResourceRetention.Resource]
        if let preparation = preparation {
            resources = try preparation.resources(for: Array(ids))
        } else {
            resources = try ids.sorted().map { id in
                guard let resource = source.first(where: { $0.id == id }) else { throw DraftPocketError.mediaRetentionUnavailable }
                return resource
            }
        }
        try graph.set(.pending(peerId: peerId, globallyUniqueId: globalId, operationId: marker.operationId), resources: resources)
        consumed.insert(marker.operationId)
    }
    if consumed.isEmpty { return nil }
    graph.pendingSends.removeAll { consumed.contains($0.id) }
    graph.entries.removeAll { $0.owner.kind == 5 && consumed.contains($0.owner.itemId ?? "") }
    return try graph.encodedForCommit()
}

func draftPocketAllowedPendingMessageIds(transaction: Transaction, mediaBox: MediaBox, ids: Set<MessageId>) -> Set<MessageId> {
    if mediaBox.draftResourceRecoverySucceeded { return ids }
    var graph: DraftPocketMediaOwnership?
    return Set(ids.filter { id in
        guard let message = transaction.getMessage(id), message.attributes.contains(where: { $0 is DraftPocketSendRetentionAttribute }) else { return true }
        guard !mediaBox.draftResourceRecoveryIndexCorrupt else { return false }
        if graph == nil { graph = try? DraftPocketMediaOwnership.read(transaction: transaction) }
        guard let globalId = message.globallyUniqueId,
              let owner = graph?.entries.first(where: { $0.owner.kind == 4 && $0.owner.peerId == id.peerId.toInt64() && $0.owner.globallyUniqueId == globalId }) else { return false }
        return owner.resources.allSatisfy { !mediaBox.draftResourceRecoveryFailedIds.contains($0.id) }
    })
}

/// No timer for accounts/scopes without ownership. A single empty-graph pass can reclaim an
/// interrupted preparation; failed-message deletions are reconciled while owners remain.
func _internal_managedDraftPocketMediaCollection(postbox: Postbox) -> Signal<Bool, NoError> {
    return postbox.preferencesView(keys: [PreferencesKeys.draftPocketMediaOwnership])
    |> mapToSignal { _ in
        return postbox.transaction { transaction -> Result<Bool, Error> in
            do { return .success(!(try DraftPocketMediaOwnership.read(transaction: transaction)).entries.isEmpty) }
            catch { return .failure(error) }
        }
    }
    |> mapToSignal { result -> Signal<Bool, NoError> in
        guard case let .success(hasOwners) = result else { return .single(false) }
        if hasOwners {
            return (_internal_collectDraftPocketMedia(postbox: postbox)
            |> then(.single(true) |> delay(30.0, queue: Queue.concurrentBackgroundQueue()))
            |> restart)
        } else if postbox.mediaBox.draftResourceRetention.hasPersistedResources {
            return _internal_collectDraftPocketMedia(postbox: postbox)
        } else { return .single(true) }
    }
}

/// Called after owner deletion has committed. An abandoned preparation is also reclaimed here.
/// Failed messages keep their owner: removal from the unsent view is NOT proof of completion.
func _internal_collectDraftPocketMedia(postbox: Postbox) -> Signal<Bool, NoError> {
    return Signal { subscriber in
        let disposable = MetaDisposable()
        Queue.concurrentBackgroundQueue().async {
            do {
                let preparation = try DraftPocketMediaPreparation(mediaBox: postbox.mediaBox, sources: [:])
                disposable.set(postbox.transaction { transaction -> (Result<[MediaBoxResourceRetention.Resource], Error>, DraftPocketMediaPreparation) in
                    do {
                        var graph = try DraftPocketMediaOwnership.read(transaction: transaction)
                        let previousCount = graph.entries.count
                        graph.entries.removeAll { entry in
                            if entry.owner.kind == 4, let globalId = entry.owner.globallyUniqueId {
                                guard let messageId = transaction.messageIdForGloballyUniqueMessageId(peerId: PeerId(entry.owner.peerId), id: globalId), let message = transaction.getMessage(messageId) else { return true }
                                return !Namespaces.Message.allLocal.contains(message.id.namespace)
                            }
                            guard let messageId = entry.owner.pendingMessageId else { return false }
                            // The local message remains for failed uploads and retries.
                            return transaction.getMessage(messageId) == nil
                        }
                        if graph.entries.count != previousCount {
                            let data = try graph.encodedForCommit()
                            DraftPocketMediaOwnership.commitEncoded(data, transaction: transaction)
                        }
                        return (.success(graph.allResources), preparation)
                    } catch {
                        return (.failure(error), preparation)
                    }
                }.start(next: { result, preparation in
                    // This callback runs after the SQLite commit; the result holds the lock.
                    do {
                        try preparation.session.collect(authoritativeResources: result.get())
                        preparation.session.finish()
                        subscriber.putNext(true)
                    } catch {
                        preparation.session.finish()
                        subscriber.putNext(false)
                    }
                    subscriber.putCompletion()
                }))
            } catch {
                subscriber.putNext(false)
                subscriber.putCompletion()
            }
        }
        return disposable
    }
}
