import Foundation
import Postbox
import SwiftSignalKit

public struct DraftPocketSendRecovery: Codable {
    public let id: String
    public let peerId: Int64
    public let threadId: Int64?
    public let snapshot: DraftPocketSnapshot
    public let createdAt: Int32
}

public enum DraftPocketSendPreparationResult {
    case untracked
    case prepared(operationId: String)
    case failure(DraftPocketError)
}

func _internal_draftPocketSendRequiresRetention(postbox: Postbox, peerId: PeerId, threadId: Int64?, resourceIds: [String]?) -> Signal<Result<Bool, DraftPocketError>, NoError> {
    return postbox.transaction { transaction in
        do {
            let graph = try DraftPocketMediaOwnership.read(transaction: transaction)
            return .success(graph.entries.contains { $0.owner == .active(peerId: peerId, threadId: threadId) })
        } catch {
            if let resourceIds = resourceIds,
               !resourceIds.contains(where: { postbox.mediaBox.draftResourceRetention.hasRetainedPin(id: $0) }) {
                return .success(false)
            }
            return .failure(.corruptStorage)
        }
    }
}

/// Must complete before any input-clear callback, history-transition hook or outgoing transform.
/// Cancellation simply leaves this visible recovery entry. It never overwrites active input.
func _internal_prepareDraftPocketSend(postbox: Postbox, peerId: PeerId, threadId: Int64?, snapshot: DraftPocketSnapshot) -> Signal<DraftPocketSendPreparationResult, NoError> {
    if draftPocketRetainedResourceIds(in: snapshot.resolvedContent)?.isEmpty == true { return .single(.untracked) }
    return postbox.transaction { transaction -> Result<Bool, Error> in
        do {
            let graph = try DraftPocketMediaOwnership.read(transaction: transaction)
            return .success(graph.entries.contains { $0.owner == .active(peerId: peerId, threadId: threadId) })
        } catch { return .failure(error) }
    }
    |> mapToSignal { tracked in
        return Signal { subscriber in
            let disposable = MetaDisposable()
            Queue.concurrentBackgroundQueue().async {
                do {
                    guard try tracked.get() else {
                        subscriber.putNext(.untracked)
                        subscriber.putCompletion()
                        return
                    }
                    postbox.mediaBox.dataQueue.sync {}
                    var sources: [MediaResourceId: String] = [:]
                    for resource in try draftPocketMediaResources(snapshot.resolvedContent) {
                        sources[resource.id] = (resource as? LocalFileReferenceMediaResource)?.localFilePath ?? postbox.mediaBox.resourcePath(resource)
                    }
                    let preparation = try DraftPocketMediaPreparation(mediaBox: postbox.mediaBox, sources: sources)
                    disposable.set(postbox.transaction { transaction -> (DraftPocketSendPreparationResult, DraftPocketMediaPreparation) in
                        // This runs even when cancellation suppresses the transaction's next
                        // callback. The queued collector waits until the result-held lease ends.
                        defer { draftPocketSchedulePreparationCollection(postbox: postbox) }
                        do {
                            var graph = try DraftPocketMediaOwnership.read(transaction: transaction)
                            guard graph.entries.contains(where: { $0.owner == .active(peerId: peerId, threadId: threadId) }) else { return (.failure(.conflict), preparation) }
                            // Repeated taps/windows sharing the same captured body reuse one operation.
                            let snapshotData = try AdaptedPostboxEncoder().encode(snapshot)
                            for existing in graph.pendingSends where existing.peerId == peerId.toInt64() && existing.threadId == threadId {
                                if try AdaptedPostboxEncoder().encode(existing.snapshot) == snapshotData { return (.prepared(operationId: existing.id), preparation) }
                            }
                            guard graph.pendingSends.count < draftPocketItemLimit else { return (.failure(.full), preparation) }
                            let operationId = UUID().uuidString
                            let recovery = DraftPocketSendRecovery(id: operationId, peerId: peerId.toInt64(), threadId: threadId, snapshot: snapshot, createdAt: Int32(clamping: Int64(Date().timeIntervalSince1970)))
                            graph.pendingSends.append(recovery)
                            try graph.set(.sending(peerId: peerId, threadId: threadId, operationId: operationId), resources: preparation.resources(for: sources.keys.map { $0.stringRepresentation }))
                            let data = try graph.encodedForCommit()
                            DraftPocketMediaOwnership.commitEncoded(data, transaction: transaction)
                            return (.prepared(operationId: operationId), preparation)
                        } catch { return (.failure(.encodingFailed), preparation) }
                    }.start(next: { result, preparation in
                        preparation.session.finish()
                        subscriber.putNext(result)
                        subscriber.putCompletion()
                        draftPocketSchedulePreparationCollection(postbox: postbox)
                    }))
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

/// Recovery is explicit and capacity checked. A full pocket leaves the recovery entry intact;
/// newer active text is never replaced. Undo/deletion use the normal card lifecycle afterward.
func _internal_recoverDraftPocketSend(postbox: Postbox, operationId: String, expectedRevision: Int64) -> Signal<DraftPocketResult, NoError> {
    return Signal { subscriber in
        let disposable = MetaDisposable()
        Queue.concurrentBackgroundQueue().async {
            do {
                let preparation = try DraftPocketMediaPreparation(mediaBox: postbox.mediaBox, sources: [:])
                try preparation.session.recoverOriginals()
                disposable.set(postbox.transaction { transaction -> (DraftPocketResult, DraftPocketMediaPreparation) in
                    do {
                        var graph = try DraftPocketMediaOwnership.read(transaction: transaction)
                        guard let recovery = graph.pendingSends.first(where: { $0.id == operationId }) else { return (.failure(.itemNotFound), preparation) }
                        let peerId = PeerId(recovery.peerId)
                        var pocket = try draftPocketRead(transaction: transaction, peerId: peerId, threadId: recovery.threadId)
                        guard pocket.revision == expectedRevision, pocket.revision < Int64.max else { return (.failure(.conflict), preparation) }
                        guard pocket.items.count + pocket.deletedItems.count < draftPocketItemLimit else { return (.failure(.full), preparation) }
                        let sendOwner = DraftPocketMediaOwner.sending(peerId: peerId, threadId: recovery.threadId, operationId: operationId)
                        guard let resources = graph.entries.first(where: { $0.owner == sendOwner })?.resources else { return (.failure(.corruptStorage), preparation) }
                        let item = DraftPocketItem(id: UUID().uuidString, now: Int32(clamping: Int64(Date().timeIntervalSince1970)), peerId: peerId, threadId: recovery.threadId, snapshot: recovery.snapshot)
                        pocket.items.insert(item, at: 0)
                        pocket.revision += 1
                        graph.pendingSends.removeAll { $0.id == operationId }
                        try graph.set(sendOwner, resources: [])
                        try graph.set(.card(peerId: peerId, threadId: recovery.threadId, id: item.id, deleted: false), resources: resources)
                        let pocketData = try AdaptedPostboxEncoder().encode(pocket)
                        _ = try AdaptedPostboxDecoder(validateData: true).decode(DraftPocketState.self, from: pocketData)
                        let ownershipData = try graph.encodedForCommit()
                        DraftPocketMediaOwnership.commitEncoded(ownershipData, transaction: transaction)
                        transaction.setPreferencesEntry(key: PreferencesKeys.draftPocket(peerId: peerId, threadId: recovery.threadId), value: PreferencesEntry(data: pocketData))
                        return (.success(_internal_draftPocketView(transaction: transaction, peerId: peerId, threadId: recovery.threadId)), preparation)
                    } catch { return (.failure(.corruptStorage), preparation) }
                }.start(next: { result, preparation in
                    preparation.session.finish()
                    subscriber.putNext(result)
                    subscriber.putCompletion()
                }))
            } catch {
                subscriber.putNext(.failure(.mediaRetentionUnavailable))
                subscriber.putCompletion()
            }
        }
        return disposable
    }
}
