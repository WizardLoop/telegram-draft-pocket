import Foundation
import Postbox
import SwiftSignalKit
import XCTest
@testable import TelegramCore

final class DraftPocketMediaLifecycleTests: XCTestCase {
    private var directory: URL!
    private var postbox: Postbox!
    private let peerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1001))

    override func setUpWithError() throws {
        initializeAccountManagement()
        self.directory = FileManager.default.temporaryDirectory.appendingPathComponent("draft-pocket-tests-" + UUID().uuidString)
        self.postbox = try self.open(self.directory)
    }

    override func tearDownWithError() throws {
        self.close()
        if let directory = self.directory {
            try? FileManager.default.removeItem(at: directory)
        }
    }

    private func open(_ directory: URL) throws -> Postbox {
        let encryption = ValueBoxEncryptionParameters(forceEncryptionIfNoSet: false, key: try XCTUnwrap(.init(data: Data(count: 32))), salt: try XCTUnwrap(.init(data: Data(count: 16))))
        let result = try self.value(openPostbox(basePath: directory.path, seedConfiguration: telegramPostboxSeedConfiguration, encryptionParameters: encryption, timestampForAbsoluteTimeBasedOperations: 1000, isMainProcess: true, isTemporary: false, isReadOnly: false, useCopy: false, useCaches: true, removeDatabaseOnError: false)
            |> filter { result in
                if case .upgrading = result { return false }
                return true
            })
        guard case let .postbox(postbox) = result else {
            throw NSError(domain: "DraftPocketTests", code: 1)
        }
        return postbox
    }

    private func close() {
        if let postbox = self.postbox {
            _ = try? self.value(postbox.transaction { _ in })
            _ = try? self.value(postbox.mediaBox.storageBox.all())
            _ = try? self.value(postbox.mediaBox.cacheStorageBox.all())
            postbox.mediaBox.dataQueue.sync {}
        }
        self.postbox = nil
        Postbox.sharedQueue.sync {}
    }

    private func value<T>(_ signal: Signal<T, NoError>, file: StaticString = #filePath, line: UInt = #line) throws -> T {
        let completed = self.expectation(description: "Postbox transaction completes")
        let result = Atomic<T?>(value: nil)
        let disposable = (signal |> take(1)).start(next: { resultValue in
            _ = result.swap(resultValue)
        }, completed: {
            completed.fulfill()
        })
        defer { disposable.dispose() }
        self.wait(for: [completed], timeout: 15)
        return try XCTUnwrap(result.with { $0 }, file: file, line: line)
    }

    private func opaque(_ text: String) -> OpaqueChatInterfaceState {
        let sync = text.isEmpty ? nil : SynchronizeableChatInputState(replySubject: nil, text: text, entities: [], timestamp: 1000, textSelection: nil, messageEffectId: nil, suggestedPost: nil)
        return OpaqueChatInterfaceState(opaqueData: Data(text.utf8), historyScrollMessageIndex: nil, mediaDraftState: nil, synchronizeableInputState: sync)
    }

    private func snapshot(_ text: String) throws -> DraftPocketSnapshot {
        let content = ChatInputContent(blocks: [.paragraph(ChatInputParagraph(runs: [ChatInputRun(text: text)]))])
        return try DraftPocketSnapshot(content: content, selection: ChatInputSelection(nsRange: NSRange(location: content.length, length: 0), in: content), state: self.opaque(text))
    }

    private func view(threadId: Int64? = nil) throws -> DraftPocketView {
        return try self.value(_internal_draftPocket(postbox: self.postbox, peerId: self.peerId, threadId: threadId))
    }

    private func perform(_ operation: DraftPocketOperation, view: DraftPocketView? = nil, threadId: Int64? = nil) throws -> DraftPocketResult {
        let view = try view ?? self.view(threadId: threadId)
        return try self.value(_internal_updateDraftPocketRetainingMedia(postbox: self.postbox, peerId: self.peerId, threadId: threadId, expectedRevision: view.revision, expectedActiveState: view.activeStateToken, operation: operation))
    }

    private func success(_ result: DraftPocketResult, file: StaticString = #filePath, line: UInt = #line) throws -> DraftPocketView {
        guard case let .success(view) = result else {
            XCTFail("Expected committed transaction", file: file, line: line)
            throw NSError(domain: "DraftPocketTests", code: 2)
        }
        return view
    }

    private func assertFailure(_ result: DraftPocketResult, _ expected: DraftPocketError, file: StaticString = #filePath, line: UInt = #line) {
        guard case let .failure(error) = result else {
            return XCTFail("Expected rejected transaction", file: file, line: line)
        }
        XCTAssertEqual(error, expected, file: file, line: line)
    }

    private func transaction<T>(_ f: @escaping (Transaction) throws -> T) throws -> T {
        return try self.value(self.postbox.transaction { transaction in Result { try f(transaction) } }).get()
    }

    private func localReferenceSnapshot() throws -> (DraftPocketSnapshot, LocalFileReferenceMediaResource, Data, URL) {
        let external = self.directory.appendingPathComponent("temporary-attachment.mov")
        let bytes = Data((0 ..< 77_003).map { UInt8($0 % 241) })
        try bytes.write(to: external, options: .atomic)
        let resource = LocalFileReferenceMediaResource(localFilePath: external.path, randomId: 99, isUniquelyReferencedTemporaryFile: true, size: Int64(bytes.count))
        let file = TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.LocalFile, id: 99), partialReference: nil, resource: resource, previewRepresentations: [], videoThumbnails: [], immediateThumbnailData: nil, mimeType: "video/mp4", size: Int64(bytes.count), attributes: [], alternativeRepresentations: [])
        let content = ChatInputContent(blocks: [.media(ChatInputMedia(media: file, kind: .video, naturalSize: ChatInputSize(width: 40, height: 30))), .paragraph(ChatInputParagraph(runs: [ChatInputRun(text: "caption")]))])
        let selection = ChatInputSelection(nsRange: NSRange(location: content.length, length: 0), in: content)
        return (try DraftPocketSnapshot(content: content, selection: selection, state: self.opaque("caption")), resource, bytes, external)
    }

    private func restoredReference() throws -> (DraftPocketSnapshot, LocalFileReferenceMediaResource, Data, URL) {
        let fixture = try self.localReferenceSnapshot()
        let saved = try self.success(self.perform(.putAside(snapshot: fixture.0, clearedState: self.opaque(""))))
        _ = try self.success(self.perform(.restore(id: saved.items[0].id, current: nil, restoredState: fixture.0.interfaceState()), view: saved))
        return fixture
    }

    private func prepare(_ snapshot: DraftPocketSnapshot) throws -> String {
        let result = try self.value(_internal_prepareDraftPocketSend(postbox: self.postbox, peerId: self.peerId, threadId: nil, snapshot: snapshot))
        guard case let .prepared(operationId) = result else { throw NSError(domain: "DraftPocketMediaLifecycleTests.prepare", code: 1) }
        return operationId
    }

    private func clearActiveOwnershipAndPersist(_ text: String) throws {
        let token = try draftPocketActiveStateToken(self.opaque(text))
        try self.transaction { transaction in
            var graph = try DraftPocketMediaOwnership.read(transaction: transaction)
            try graph.set(.active(peerId: self.peerId, threadId: nil), resources: [])
            let encoded = try graph.encodedForCommit()
            DraftPocketMediaOwnership.commitEncoded(encoded, transaction: transaction)
            transaction.setPeerChatInterfaceState(self.peerId, state: StoredPeerChatInterfaceState(overrideChatTimestamp: nil, historyScrollMessageIndex: nil, associatedMessageIds: [], data: token))
        }
    }

    func testLocalFileReferenceSurvivesDeletedExternalPathColdReopenAndForcedCleanup() throws {
        let (snapshot, resource, bytes, external) = try self.localReferenceSnapshot()
        let saved = try self.success(self.perform(.putAside(snapshot: snapshot, clearedState: self.opaque(""))))
        try FileManager.default.removeItem(at: external)
        self.close()
        self.postbox = try self.open(self.directory)
        let reopened = try self.view()
        XCTAssertEqual(reopened.items[0].id, saved.items[0].id)
        XCTAssertEqual(try self.value(self.postbox.mediaBox.removeCachedResourcesWithResult([resource.id], force: true)), [])
        let restored = try self.success(self.perform(.restore(id: reopened.items[0].id, current: nil, restoredState: snapshot.interfaceState()), view: reopened))
        XCTAssertTrue(restored.items.isEmpty)
        // This is the actual resource type and complete-data read used by media consumers.
        let data = try self.value(self.postbox.mediaBox.resourceData(resource, attemptSynchronously: true))
        XCTAssertTrue(data.complete)
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: data.path)), bytes)
        XCTAssertFalse(FileManager.default.fileExists(atPath: external.path))
    }

    func testPreparedSendSurvivesClearCrashAndRecoversWithoutOverwritingNewerText() throws {
        let (snapshot, resource, bytes, external) = try self.restoredReference()
        let operationId = try self.prepare(snapshot)
        try self.clearActiveOwnershipAndPersist("newer input")
        try FileManager.default.removeItem(at: external)
        self.close()
        self.postbox = try self.open(self.directory)
        XCTAssertTrue(try self.value(_internal_collectDraftPocketMedia(postbox: self.postbox)))
        let view = try self.view()
        XCTAssertEqual(view.pendingSends.map { $0.id }, [operationId])
        let recovered = try self.success(self.value(_internal_recoverDraftPocketSend(postbox: self.postbox, operationId: operationId, expectedRevision: view.revision)))
        XCTAssertEqual(recovered.activeStateToken, view.activeStateToken)
        XCTAssertTrue(recovered.pendingSends.isEmpty)
        XCTAssertEqual(recovered.items[0].snapshot.content, snapshot.content)
        let data = try self.value(self.postbox.mediaBox.resourceData(resource, attemptSynchronously: true))
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: data.path)), bytes)
    }

    func testSharedMediaSwapAtCapacityDeleteUndoAndLastOwnerRelease() throws {
        let (snapshot, resource, bytes, _) = try self.localReferenceSnapshot()
        for _ in 0 ..< draftPocketItemLimit {
            _ = try self.success(self.perform(.putAside(snapshot: snapshot, clearedState: self.opaque(""))))
        }
        let full = try self.view()
        let swapped = try self.success(self.perform(.restore(id: full.items[0].id, current: snapshot, restoredState: snapshot.interfaceState()), view: full))
        XCTAssertEqual(swapped.items.count, draftPocketItemLimit)
        let firstId = swapped.items[0].id
        let deleted = try self.success(self.perform(.delete(id: firstId), view: swapped))
        XCTAssertEqual(deleted.deletedItems.count, 1)
        XCTAssertTrue(try self.value(_internal_collectDraftPocketMedia(postbox: self.postbox)))
        XCTAssertEqual(try self.value(self.postbox.mediaBox.removeCachedResourcesWithResult([resource.id], force: true)), [])
        let undone = try self.success(self.perform(.undoDelete(id: firstId), view: deleted))
        XCTAssertEqual(undone.items.count, draftPocketItemLimit)
        for item in undone.items {
            _ = try self.success(self.perform(.delete(id: item.id)))
            _ = try self.success(self.perform(.finalizeDelete(id: item.id)))
        }
        XCTAssertTrue(try self.view().items.isEmpty)
        XCTAssertTrue(try self.value(_internal_collectDraftPocketMedia(postbox: self.postbox)))
        let activeBytes = try self.value(self.postbox.mediaBox.resourceData(resource, attemptSynchronously: true))
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: activeBytes.path)), bytes)
        try self.clearActiveOwnershipAndPersist("")
        XCTAssertTrue(try self.value(_internal_collectDraftPocketMedia(postbox: self.postbox)))
        XCTAssertEqual(try self.value(self.postbox.mediaBox.removeCachedResourcesWithResult([resource.id], force: true)), [resource.id])
    }

    func testPendingHandoffFailedRetryAndFinalOwnerReleaseUseRealMessagesAndBytes() throws {
        let (snapshot, resource, bytes, external) = try self.restoredReference()
        let operationId = try self.prepare(snapshot)
        try self.clearActiveOwnershipAndPersist("")
        let file = try XCTUnwrap(snapshot.content.allMedia.first?._asMedia())
        let marker = DraftPocketSendRetentionAttribute(operationId: operationId)
        let sourceId = MessageId(peerId: self.peerId, namespace: Namespaces.Message.Local, id: 111)
        let source = StoreMessage(id: sourceId, customStableId: nil, globallyUniqueId: 1111, groupingKey: nil, threadId: nil, timestamp: 1000, flags: [.Failed], tags: [], globalTags: [], localTags: [], forwardInfo: nil, authorId: nil, text: "caption", attributes: [marker], media: [file])
        let preparation = try DraftPocketMediaPreparation(mediaBox: self.postbox.mediaBox, sources: [resource.id: external.path])
        _ = try self.transaction { transaction -> DraftPocketMediaPreparation in
            let data = try XCTUnwrap(try draftPocketPendingOwnershipData(transaction: transaction, peerId: self.peerId, messages: [source], preparation: preparation))
            DraftPocketMediaOwnership.commitEncoded(data, transaction: transaction)
            _ = transaction.addMessages([source], location: .Random)
            return preparation
        }
        preparation.session.finish()
        try FileManager.default.removeItem(at: external)
        XCTAssertTrue(try self.value(_internal_collectDraftPocketMedia(postbox: self.postbox)), "failed local messages keep ownership")
        XCTAssertTrue(try self.view().pendingSends.isEmpty)
        let targetId = MessageId(peerId: self.peerId, namespace: Namespaces.Message.Local, id: 112)
        let target = StoreMessage(id: targetId, customStableId: nil, globallyUniqueId: 1112, groupingKey: nil, threadId: nil, timestamp: 1001, flags: [.Failed], tags: [], globalTags: [], localTags: [], forwardInfo: nil, authorId: nil, text: "retry", attributes: [marker], media: [file])
        try self.transaction { transaction in
            XCTAssertThrowsError(try draftPocketPendingOwnershipData(transaction: transaction, peerId: self.peerId, messages: [target], preparation: nil), "A second ordinary enqueue cannot impersonate Retry")
            XCTAssertNil(transaction.getMessage(targetId))
            XCTAssertNotNil(transaction.getMessage(sourceId))
        }
        try self.transaction { transaction in
            let data = try XCTUnwrap(try draftPocketPendingOwnershipData(transaction: transaction, peerId: self.peerId, messages: [target], preparation: nil, retryingMessageIds: [sourceId]))
            DraftPocketMediaOwnership.commitEncoded(data, transaction: transaction)
            _ = transaction.addMessages([target], location: .Random)
            _internal_deleteMessages(transaction: transaction, mediaBox: self.postbox.mediaBox, ids: [sourceId], deleteMedia: false)
        }
        XCTAssertTrue(try self.value(_internal_collectDraftPocketMedia(postbox: self.postbox)))
        let data = try self.value(self.postbox.mediaBox.resourceData(resource, attemptSynchronously: true))
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: data.path)), bytes)
        try self.transaction { transaction in
            _internal_deleteMessages(transaction: transaction, mediaBox: self.postbox.mediaBox, ids: [targetId], deleteMedia: false)
        }
        XCTAssertTrue(try self.value(_internal_collectDraftPocketMedia(postbox: self.postbox)))
        XCTAssertEqual(try self.value(self.postbox.mediaBox.removeCachedResourcesWithResult([resource.id], force: true)), [resource.id])
    }

    func testCorruptPocketOwnershipDoesNotBlockOrdinarySendOrComposerAutosave() throws {
        let corrupt = Data([0xff, 0x00, 0x7f])
        let active = self.opaque("new text remains durable")
        let expected = try draftPocketActiveStateToken(active)
        try self.transaction { transaction in
            transaction.setPreferencesEntry(key: PreferencesKeys.draftPocketMediaOwnership, value: PreferencesEntry(data: corrupt))
            let message = StoreMessage(id: MessageId(peerId: self.peerId, namespace: Namespaces.Message.Local, id: 150), customStableId: nil, globallyUniqueId: 1500, groupingKey: nil, threadId: nil, timestamp: 1000, flags: [], tags: [], globalTags: [], localTags: [], forwardInfo: nil, authorId: nil, text: "ordinary message", attributes: [], media: [])
            XCTAssertNil(try draftPocketPendingOwnershipData(transaction: transaction, peerId: self.peerId, messages: [message], preparation: nil))
            XCTAssertFalse(_internal_setOpaqueChatInterfaceState(transaction: transaction, peerId: self.peerId, threadId: nil, state: active, retainedDraftResourceIds: []))
            XCTAssertEqual(transaction.getPeerChatInterfaceState(self.peerId)?.data, expected)
            XCTAssertEqual(transaction.getPreferencesEntry(key: PreferencesKeys.draftPocketMediaOwnership)?.data, corrupt)
        }
        let untracked = try self.value(_internal_draftPocketSendRequiresRetention(postbox: self.postbox, peerId: self.peerId, threadId: nil, resourceIds: ["local-file-never-retained"]))
        XCTAssertFalse(try untracked.get())
    }
}
