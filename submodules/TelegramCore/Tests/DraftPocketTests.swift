import Foundation
import Postbox
import SwiftSignalKit
import XCTest
@testable import TelegramCore

/// Uses the production codec and real Postbox transactions, including reopen-on-disk.
/// These are simulator tests, not a substitute package with duplicate models.
final class DraftPocketTests: XCTestCase {
    func testSelectionAffinityCodecRemainsCompatibleWithOlderCards() throws {
        struct Step: Codable { var block: Int32; var slot: Int32 }
        struct Position: Codable { var path: [Step]; var offset: Int32; var affinity: Int32? }
        struct Selection: Codable { var start: Position; var end: Position }
        let start = ChatInputPosition(path: [.init(blockIndex: 1, slot: 1)], offset: 2, affinity: .upstream)
        let end = ChatInputPosition(path: start.path, offset: 2, affinity: .downstream)
        let selection = ChatInputSelection(start: start, end: end)
        XCTAssertTrue(selection.isCollapsed, "affinity must not turn a text caret into a nonempty range")
        let encoded = try AdaptedPostboxEncoder().encode(DraftPocketSelection(selection))
        XCTAssertEqual(try AdaptedPostboxDecoder().decode(DraftPocketSelection.self, from: encoded).value, selection)

        let oldPosition = Position(path: [Step(block: 0, slot: 0)], offset: 3, affinity: nil)
        let oldData = try AdaptedPostboxEncoder().encode(Selection(start: oldPosition, end: oldPosition))
        let old = try AdaptedPostboxDecoder().decode(DraftPocketSelection.self, from: oldData).value
        XCTAssertNil(old.start.affinity)
        XCTAssertEqual(old.start.offset, 3)
        let unknownPosition = Position(path: oldPosition.path, offset: 3, affinity: 42)
        let unknownData = try AdaptedPostboxEncoder().encode(Selection(start: unknownPosition, end: unknownPosition))
        XCTAssertThrowsError(try AdaptedPostboxDecoder().decode(DraftPocketSelection.self, from: unknownData))
    }

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
        let encoder = PostboxEncoder()
        encoder.encodeString(text, forKey: "fixtureText")
        return OpaqueChatInterfaceState(opaqueData: encoder.makeData(), historyScrollMessageIndex: nil, mediaDraftState: nil, synchronizeableInputState: sync)
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
        return try self.value(_internal_updateDraftPocket(postbox: self.postbox, peerId: self.peerId, threadId: threadId, expectedRevision: view.revision, expectedActiveState: view.activeStateToken, operation: operation))
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

    func testRealCodecKeepsStructureEmojiFilesReplyAndSelection() throws {
        let file = TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.CloudFile, id: 9_000_000_000), partialReference: nil, resource: LocalFileMediaResource(fileId: 17), previewRepresentations: [], videoThumbnails: [], immediateThumbnailData: nil, mimeType: "application/x-tgsticker", size: nil, attributes: [.CustomEmoji(isPremium: false, isSingleColor: false, alt: "👨‍👩‍👧‍👦", packReference: nil)], alternativeRepresentations: [])
        let nested = ChatInputContent(blocks: [.paragraph(ChatInputParagraph(runs: [
            ChatInputRun(text: "שלום e\u{301} ", attributes: ChatInputInlineAttributes(bold: true)),
            ChatInputRun(text: "👨‍👩‍👧‍👦", attributes: ChatInputInlineAttributes(entity: .customEmoji(fileId: file.fileId.id, file: file, enableAnimation: true)))
        ]))])
        let content = ChatInputContent(blocks: [.blockQuote(ChatInputBlockQuote(content: nested, collapsed: true))])
        let position = ChatInputPosition(path: [ChatInputPathStep(blockIndex: 0), ChatInputPathStep(blockIndex: 0)], offset: 7, affinity: .upstream)
        let selection = ChatInputSelection(start: position, end: position)
        let reply = EngineMessageReplySubject(messageId: MessageId(peerId: self.peerId, namespace: Namespaces.Message.Cloud, id: 7), quote: EngineMessageReplyQuote(text: "quote", offset: 2, entities: [], media: nil, richText: nil), innerSubject: nil)
        let sync = SynchronizeableChatInputState(replySubject: reply, text: "ignored preview", entities: [], timestamp: 123, textSelection: nil, messageEffectId: nil, suggestedPost: nil)
        let state = OpaqueChatInterfaceState(opaqueData: self.opaque("opaque payload").opaqueData, historyScrollMessageIndex: nil, mediaDraftState: nil, synchronizeableInputState: sync)
        let snapshot = try DraftPocketSnapshot(content: content, selection: selection, state: state)
        let encoded = try AdaptedPostboxEncoder().encode(snapshot)
        let decoded = try AdaptedPostboxDecoder().decode(DraftPocketSnapshot.self, from: encoded)
        XCTAssertEqual(decoded.content, content)
        XCTAssertEqual(decoded.selection, selection)
        XCTAssertEqual(try decoded.interfaceState().synchronizeableInputState?.replySubject, reply)
        XCTAssertEqual(decoded.resolvedContent.draftPocketCustomEmojiFiles()[file.fileId.id]?.fileId, file.fileId)
        XCTAssertTrue(decoded.resolvedContent.unresolvedCustomEmojiFileIds().isEmpty)
    }

    func testPutAsideCommitsBeforeClearAndRejectsRepeatedRequest() throws {
        let baseline = try self.view()
        let operation = DraftPocketOperation.putAside(snapshot: try self.snapshot("original"), clearedState: self.opaque(""))
        let committed = try self.success(self.perform(operation, view: baseline))
        XCTAssertEqual(committed.items.count, 1)
        XCTAssertEqual(committed.activeStateToken, try draftPocketActiveStateToken(self.opaque("")))
        self.assertFailure(try self.perform(operation, view: baseline), .conflict)
        XCTAssertEqual(try self.view().items.count, 1)
    }

    func testRestoreSwapDeleteUndoAndSlotReservation() throws {
        let saved = try self.success(self.perform(.putAside(snapshot: self.snapshot("long draft"), clearedState: self.opaque(""))))
        let id = try XCTUnwrap(saved.items.first?.id)
        let swapped = try self.success(self.perform(.restore(id: id, current: self.snapshot("short draft"), restoredState: self.opaque("long draft")), view: saved))
        XCTAssertEqual(swapped.items.map { $0.snapshot.content.plainText }, ["short draft"])
        XCTAssertEqual(swapped.activeStateToken, try draftPocketActiveStateToken(self.opaque("long draft")))
        let otherId = try XCTUnwrap(swapped.items.first?.id)
        let deleted = try self.success(self.perform(.delete(id: otherId), view: swapped))
        XCTAssertTrue(deleted.items.isEmpty)
        XCTAssertEqual(deleted.deletedItems.count, 1)
        let peerId = self.peerId
        let newerActive = try draftPocketActiveStateToken(self.opaque("typing during Undo"))
        _ = try self.value(self.postbox.transaction { transaction in
            transaction.setPeerChatInterfaceState(peerId, state: StoredPeerChatInterfaceState(overrideChatTimestamp: nil, historyScrollMessageIndex: nil, associatedMessageIds: [], data: newerActive))
        })
        let undone = try self.success(self.perform(.undoDelete(id: otherId), view: deleted))
        XCTAssertEqual(undone.items.first?.id, otherId)
        XCTAssertTrue(undone.deletedItems.isEmpty)
        XCTAssertEqual(undone.activeStateToken, newerActive)
    }

    func testLimitRejectsGrowthButAllowsSwapAtTwenty() throws {
        for index in 0 ..< draftPocketItemLimit {
            _ = try self.success(self.perform(.putAside(snapshot: self.snapshot("draft \(index)"), clearedState: self.opaque(""))))
        }
        let full = try self.view()
        self.assertFailure(try self.perform(.putAside(snapshot: self.snapshot("overflow"), clearedState: self.opaque("")), view: full), .full)
        let swapped = try self.success(self.perform(.restore(id: full.items[0].id, current: self.snapshot("replacement"), restoredState: self.opaque("draft 19")), view: full))
        XCTAssertEqual(swapped.items.count, draftPocketItemLimit)
        XCTAssertEqual(swapped.items[0].snapshot.content.plainText, "replacement")
    }

    func testRestartAndTopicIsolationIncludingNilVersusZero() throws {
        _ = try self.success(self.perform(.putAside(snapshot: self.snapshot("chat"), clearedState: self.opaque(""))))
        _ = try self.success(self.perform(.putAside(snapshot: self.snapshot("topic zero"), clearedState: self.opaque("")), threadId: 0))
        _ = try self.success(self.perform(.putAside(snapshot: self.snapshot("topic one"), clearedState: self.opaque("")), threadId: 1))
        self.close()
        self.postbox = try self.open(self.directory)
        XCTAssertEqual(try self.view().items.first?.snapshot.content.plainText, "chat")
        XCTAssertEqual(try self.view(threadId: 0).items.first?.snapshot.content.plainText, "topic zero")
        XCTAssertEqual(try self.view(threadId: 1).items.first?.snapshot.content.plainText, "topic one")
        let otherPeer = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1002))
        XCTAssertTrue(try self.value(_internal_draftPocket(postbox: self.postbox, peerId: otherPeer, threadId: nil)).items.isEmpty)
        let otherAccount = try self.open(self.directory.appendingPathComponent("other-account"))
        XCTAssertTrue(try self.value(_internal_draftPocket(postbox: otherAccount, peerId: self.peerId, threadId: nil)).items.isEmpty)
    }

    func testChangedActiveDraftRejectsStaleControllerWithoutWritingPocket() throws {
        let baseline = try self.view()
        let peerId = self.peerId
        _ = try self.value(self.postbox.transaction { transaction in
            transaction.setPeerChatInterfaceState(peerId, state: StoredPeerChatInterfaceState(overrideChatTimestamp: nil, historyScrollMessageIndex: nil, associatedMessageIds: [], data: Data([99])))
        })
        self.assertFailure(try self.perform(.putAside(snapshot: self.snapshot("stale"), clearedState: self.opaque("")), view: baseline), .conflict)
        XCTAssertTrue(try self.view().items.isEmpty)
        XCTAssertEqual(try self.view().activeStateToken, Data([99]))
    }

    func testCorruptOrFutureSchemaDoesNotBecomeEmptyWritableStorage() throws {
        var future = DraftPocketState()
        future.schemaVersion = 2
        let data = try AdaptedPostboxEncoder().encode(future)
        let peerId = self.peerId
        _ = try self.value(self.postbox.transaction { transaction in
            transaction.setPreferencesEntry(key: PreferencesKeys.draftPocket(peerId: peerId, threadId: nil), value: PreferencesEntry(data: data))
        })
        XCTAssertEqual(try self.view().error, .corruptStorage)
        self.assertFailure(try self.perform(.putAside(snapshot: self.snapshot("keep me"), clearedState: self.opaque(""))), .corruptStorage)
        let retained = try self.value(self.postbox.transaction { transaction in
            transaction.getPreferencesEntry(key: PreferencesKeys.draftPocket(peerId: peerId, threadId: nil))?.data
        })
        XCTAssertEqual(retained, data)
    }

    func testMalformedPocketBytesStayUnchangedAndRejectTransfer() throws {
        let peerId = self.peerId
        for data in [Data([0xff, 0, 0x7f]), Data([2, 0x61]), Data([1, 0x61, 10, 0xff, 0xff, 0xff, 0xff])] {
            _ = try self.value(self.postbox.transaction { transaction in
                transaction.setPreferencesEntry(key: PreferencesKeys.draftPocket(peerId: peerId, threadId: nil), value: PreferencesEntry(data: data))
            })
            XCTAssertEqual(try self.view().error, .corruptStorage)
            self.assertFailure(try self.perform(.putAside(snapshot: self.snapshot("keep me"), clearedState: self.opaque(""))), .corruptStorage)
            XCTAssertEqual(try self.value(self.postbox.transaction { transaction in
                transaction.getPreferencesEntry(key: PreferencesKeys.draftPocket(peerId: peerId, threadId: nil))?.data
            }), data)
        }
    }

    func testSnapshotValidatesItsOpaquePostboxPayloadsBeforeDecoding() throws {
        let content = ChatInputContent(blocks: [.details(ChatInputDetails(content: ChatInputContent(blocks: [
            .table(ChatInputTable(rows: [ChatInputTableRow(cells: [ChatInputTableCell(runs: [ChatInputRun(text: "nested")])])]))
        ]), title: [ChatInputRun(text: "title")], expanded: true))])
        let selection = DraftPocketSelection(ChatInputSelection(start: .init(path: [.init(blockIndex: 0), .init(blockIndex: 0), .init(blockIndex: 0)], offset: 2), end: .init(path: [.init(blockIndex: 0), .init(blockIndex: 0), .init(blockIndex: 0)], offset: 2)))
        let contentData = try AdaptedPostboxEncoder().encode(content)
        let stateData = try draftPocketActiveStateToken(self.opaque("nested"))
        func encoded(content: Data, state: Data, emojiFiles: DraftPocketEmojiFiles) -> Data {
            let encoder = PostboxEncoder()
            encoder.encodeData(content, forKey: "content")
            encoder.encode(selection, forKey: "selection")
            encoder.encodeData(state, forKey: "interfaceState")
            encoder.encode(emojiFiles, forKey: "emojiFiles")
            return encoder.makeData()
        }
        let emojiFiles = DraftPocketEmojiFiles(content: content)
        let valid = encoded(content: contentData, state: stateData, emojiFiles: emojiFiles)
        let restored = try AdaptedPostboxDecoder(validateData: true).decode(DraftPocketSnapshot.self, from: valid)
        XCTAssertEqual(restored.content, content)
        XCTAssertEqual(restored.selection, selection.value)
        XCTAssertEqual(try restored.interfaceState().opaqueData, self.opaque("nested").opaqueData)
        for malformed in [Data([0xff, 0, 0x7f]), Data(contentData.dropLast())] {
            XCTAssertThrowsError(try AdaptedPostboxDecoder(validateData: true).decode(DraftPocketSnapshot.self, from: encoded(content: malformed, state: stateData, emojiFiles: emojiFiles)))
            XCTAssertThrowsError(try AdaptedPostboxDecoder(validateData: true).decode(DraftPocketSnapshot.self, from: encoded(content: contentData, state: malformed, emojiFiles: emojiFiles)))
        }
        let validOpaque = try XCTUnwrap(self.opaque("nested").opaqueData)
        for malformed in [Data([0xff, 0, 0x7f]), Data(validOpaque.dropLast()), Data([1, 0x61, 10, 0xff, 0xff, 0xff, 0xff])] {
            let badState = OpaqueChatInterfaceState(opaqueData: malformed, historyScrollMessageIndex: nil, mediaDraftState: nil, synchronizeableInputState: nil)
            // Reject before preparing a transfer, and reject the same corruption
            // when the outer card and InternalChatInterfaceState are well framed.
            XCTAssertThrowsError(try DraftPocketSnapshot(content: content, selection: selection.value, state: badState))
            let badStateData = try draftPocketActiveStateToken(badState)
            XCTAssertThrowsError(try AdaptedPostboxDecoder(validateData: true).decode(DraftPocketSnapshot.self, from: encoded(content: contentData, state: badStateData, emojiFiles: emojiFiles)))
        }
        let badEmoji = PostboxEncoder()
        badEmoji.encodeDataArray([Data([0xff, 0, 0x7f])], forKey: "files")
        XCTAssertThrowsError(try AdaptedPostboxDecoder(validateData: true).decode(DraftPocketEmojiFiles.self, from: badEmoji.makeData()))
    }

    func testEncodingFailureDoesNotCommitEitherHalfOfTransfer() throws {
        let peerId = self.peerId
        let original = try draftPocketActiveStateToken(self.opaque("original"))
        _ = try self.value(self.postbox.transaction { transaction in
            transaction.setPeerChatInterfaceState(peerId, state: StoredPeerChatInterfaceState(overrideChatTimestamp: nil, historyScrollMessageIndex: nil, associatedMessageIds: [], data: original))
        })
        let snapshot = try self.snapshot("original")
        let clearedState = self.opaque("")
        let result = try self.value(self.postbox.transaction { transaction in
            return _internal_updateDraftPocket(transaction: transaction, peerId: peerId, threadId: nil, expectedRevision: 0, expectedActiveState: original, operation: .putAside(snapshot: snapshot, clearedState: clearedState), now: 1000, itemId: "failure-test", encodePocket: { _ in
                throw NSError(domain: "InjectedEncoderFailure", code: 1)
            })
        })
        self.assertFailure(result, .encodingFailed)
        XCTAssertTrue(try self.view().items.isEmpty)
        XCTAssertEqual(try self.view().revision, 0)
        XCTAssertEqual(try self.view().activeStateToken, original)
    }

    func testInterruptedDeleteCanBeUndoneAfterReopen() throws {
        let saved = try self.success(self.perform(.putAside(snapshot: self.snapshot("recover delete"), clearedState: self.opaque(""))))
        let id = try XCTUnwrap(saved.items.first?.id)
        _ = try self.success(self.perform(.delete(id: id), view: saved))
        self.close()
        self.postbox = try self.open(self.directory)
        let reopened = try self.view()
        XCTAssertEqual(reopened.deletedItems.first?.id, id)
        let recovered = try self.success(self.perform(.undoDelete(id: id), view: reopened))
        XCTAssertEqual(recovered.items.first?.snapshot.content.plainText, "recover delete")
        let deleted = try self.success(self.perform(.delete(id: id), view: recovered))
        let finalized = try self.success(self.perform(.finalizeDelete(id: id), view: deleted))
        XCTAssertTrue(finalized.items.isEmpty)
        XCTAssertTrue(finalized.deletedItems.isEmpty)
    }

    func testStandaloneMediaDraftCannotBeClearedByCoreCaller() throws {
        let state = OpaqueChatInterfaceState(opaqueData: Data(), historyScrollMessageIndex: nil, mediaDraftState: MediaDraftState(contentType: .audio, timestamp: 1000), synchronizeableInputState: nil)
        let content = ChatInputContent(blocks: [.paragraph(ChatInputParagraph(runs: [ChatInputRun(text: "caption")]))])
        let snapshot = try DraftPocketSnapshot(content: content, selection: ChatInputSelection(nsRange: NSRange(location: 0, length: 0), in: content), state: state)
        self.assertFailure(try self.perform(.putAside(snapshot: snapshot, clearedState: self.opaque(""))), .unsupportedContext)
        XCTAssertTrue(try self.view().items.isEmpty)
    }

    func testMediaIsRejectedBeforeClearingComposerUntilDurableRetentionExists() throws {
        let file = TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.LocalFile, id: 1), partialReference: nil, resource: LocalFileMediaResource(fileId: 1), previewRepresentations: [], videoThumbnails: [], immediateThumbnailData: nil, mimeType: "application/octet-stream", size: nil, attributes: [], alternativeRepresentations: [])
        let content = ChatInputContent(blocks: [.media(ChatInputMedia(media: file, kind: .document, naturalSize: ChatInputSize(width: 10, height: 10)))])
        let selection = ChatInputSelection(nsRange: NSRange(location: 0, length: 0), in: content)
        let snapshot = try DraftPocketSnapshot(content: content, selection: selection, state: self.opaque("media"))
        self.assertFailure(try self.perform(.putAside(snapshot: snapshot, clearedState: self.opaque(""))), .mediaRetentionUnavailable)
        XCTAssertTrue(try self.view().items.isEmpty)
        XCTAssertNil(try self.view().activeStateToken)
    }

    func testCloudEmojiFileSidecarSurvivesCommittedPocketReopen() throws {
        // Synthetic ids are isolated unit inputs. No server call or demo asset is made.
        let resource = CloudDocumentMediaResource(datacenterId: 1, fileId: 42, accessHash: 1, size: 8, fileReference: Data([1]), fileName: nil)
        let file = TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.CloudFile, id: 42), partialReference: nil, resource: resource, previewRepresentations: [], videoThumbnails: [], immediateThumbnailData: nil, mimeType: "application/x-tgsticker", size: 8, attributes: [.CustomEmoji(isPremium: false, isSingleColor: false, alt: "✅", packReference: nil)], alternativeRepresentations: [])
        let content = ChatInputContent(blocks: [.paragraph(ChatInputParagraph(runs: [ChatInputRun(text: "✅", attributes: ChatInputInlineAttributes(entity: .customEmoji(fileId: 42, file: file, enableAnimation: true)))]))])
        let snapshot = try DraftPocketSnapshot(content: content, selection: ChatInputSelection(nsRange: NSRange(location: 1, length: 0), in: content), state: self.opaque("✅"))
        _ = try self.success(self.perform(.putAside(snapshot: snapshot, clearedState: self.opaque(""))))
        self.close()
        self.postbox = try self.open(self.directory)
        let decoded = try XCTUnwrap(self.view().items.first?.snapshot)
        let restoredFile = try XCTUnwrap(decoded.resolvedContent.draftPocketCustomEmojiFiles()[42])
        XCTAssertTrue(restoredFile.isEqual(to: file))
        XCTAssertEqual(decoded.selection, snapshot.selection)
    }
}
