import Foundation
import XCTest
import TelegramCore
import AccountContext
import ChatInterfaceState

final class DraftPocketComposerStateTests: XCTestCase {
    private func fixture() -> ChatTextInputState {
        let file = TelegramMediaFile(fileId: EngineMedia.Id(namespace: Namespaces.Media.CloudFile, id: 9_000_000_001), partialReference: nil, resource: LocalFileMediaResource(fileId: 7), previewRepresentations: [], videoThumbnails: [], immediateThumbnailData: nil, mimeType: "application/x-tgsticker", size: nil, attributes: [.CustomEmoji(isPremium: false, isSingleColor: false, alt: "👨‍👩‍👧‍👦", packReference: nil)], alternativeRepresentations: [])
        let body = ChatInputContent(blocks: [.paragraph(ChatInputParagraph(runs: [
            ChatInputRun(text: "שלום e\u{301} ", attributes: ChatInputInlineAttributes(bold: true)),
            ChatInputRun(text: "👨‍👩‍👧‍👦", attributes: ChatInputInlineAttributes(entity: .customEmoji(fileId: file.fileId.id, file: file, enableAnimation: true)))
        ]))])
        let content = ChatInputContent(blocks: [.blockQuote(ChatInputBlockQuote(content: body, collapsed: true))])
        var input = ChatTextInputState(content: content, selectionRange: 0 ..< 0)
        let position = ChatInputPosition(path: [ChatInputPathStep(blockIndex: 0), ChatInputPathStep(blockIndex: 0)], offset: 7)
        input.selection = ChatInputSelection(start: position, end: position)
        return input
    }

    func testActiveStateCodecKeepsNestedSelectionAndEmojiFile() throws {
        let input = self.fixture()
        let encoded = try EngineEncoder.encode(input)
        let decoded = try EngineDecoder.decode(ChatTextInputState.self, from: encoded)
        XCTAssertEqual(decoded.content, input.content)
        XCTAssertEqual(decoded.selection, input.selection)
        XCTAssertTrue(decoded.content.unresolvedCustomEmojiFileIds().isEmpty)
    }

    func testPocketValidatedDecodeRejectsMalformedOpaqueComposer() throws {
        let input = self.fixture()
        let state = ChatInterfaceState().withUpdatedComposeInputState(input).withUpdatedInputLanguage("he")
        let encoded = try EngineEncoder.encode(state)
        let decoded = try EngineDecoder.decodeValidated(ChatInterfaceState.self, from: encoded)
        XCTAssertEqual(decoded.composeInputState, input)
        XCTAssertEqual(decoded.inputLanguage, "he")
        for malformed in [Data([0xff, 0, 0x7f]), Data(encoded.dropLast()), Data([1, 0x61, 10, 0xff, 0xff, 0xff, 0xff])] {
            XCTAssertThrowsError(try EngineDecoder.decodeValidated(ChatInterfaceState.self, from: malformed))
        }
    }

    func testRelaunchKeepsCanonicalComposerWhenCloudProjectionMatches() throws {
        let input = self.fixture()
        let peerId = EnginePeer.Id(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1001))
        let reply = ChatInterfaceState.ReplyMessageSubject(messageId: EngineMessage.Id(peerId: peerId, namespace: Namespaces.Message.Cloud, id: 8), quote: EngineMessageReplyQuote(text: "שלום", offset: 0, entities: [], media: nil, richText: nil), innerSubject: nil)
        let state = ChatInterfaceState().withUpdatedComposeInputState(input).withUpdatedReplyMessageSubject(reply).withUpdatedInputLanguage("he").withUpdatedComposeDisableUrlPreviews(["https://example.org"]).withUpdatedTimestamp(100)
        let opaque = OpaqueChatInterfaceState(opaqueData: try EngineEncoder.encode(state), historyScrollMessageIndex: nil, mediaDraftState: nil, synchronizeableInputState: state.synchronizeableInputState)
        let parsed = ChatInterfaceState.parse(opaque)
        XCTAssertEqual(parsed.composeInputState, input)
        XCTAssertEqual(parsed.replyMessageSubject, reply)
        XCTAssertEqual(parsed.inputLanguage, "he")
        XCTAssertEqual(parsed.composeDisableUrlPreviews, ["https://example.org"])
        XCTAssertTrue(parsed.composeInputState.content.unresolvedCustomEmojiFileIds().isEmpty)
    }

    func testChangedRemoteProjectionStillReplacesLocalActiveDraft() throws {
        let local = ChatInterfaceState().withUpdatedComposeInputState(self.fixture()).withUpdatedTimestamp(100)
        let remote = SynchronizeableChatInputState(replySubject: nil, text: "Remote update", entities: [], timestamp: 101, textSelection: nil, messageEffectId: nil, suggestedPost: nil)
        let opaque = OpaqueChatInterfaceState(opaqueData: try EngineEncoder.encode(local), historyScrollMessageIndex: nil, mediaDraftState: nil, synchronizeableInputState: remote)
        XCTAssertEqual(ChatInterfaceState.parse(opaque).composeInputState.content.plainText, "Remote update")
    }

    func testDelayedClearRejectsIdenticalDraftRestoredByPocket() {
        let peerId = EnginePeer.Id(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1001))
        let input = self.fixture()
        let state = ChatInterfaceState().withUpdatedComposeInputState(input)
        let pending = ChatInputClearGuard(peerId: peerId, threadId: nil, generation: 4, input: input, state: state)
        XCTAssertTrue(pending.matches(peerId: peerId, threadId: nil, generation: 4, input: input, state: state))
        // A successful restore replaces the draft even if its entire canonical value is equal.
        XCTAssertFalse(pending.matches(peerId: peerId, threadId: nil, generation: 5, input: input, state: state))
    }

    func testDelayedClearRejectsNewInputSelectionAndReplyContext() {
        let peerId = EnginePeer.Id(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1001))
        let input = self.fixture()
        let state = ChatInterfaceState().withUpdatedComposeInputState(input)
        let pending = ChatInputClearGuard(peerId: peerId, threadId: nil, generation: 1, input: input, state: state)
        let newer = ChatTextInputState(inputText: NSAttributedString(string: "Keep this newer draft"))
        XCTAssertFalse(pending.matches(peerId: peerId, threadId: nil, generation: 1, input: newer, state: state))
        var movedCaret = input
        let position = ChatInputPosition(path: [ChatInputPathStep(blockIndex: 0), ChatInputPathStep(blockIndex: 0)], offset: 0)
        movedCaret.selection = ChatInputSelection(start: position, end: position)
        XCTAssertFalse(pending.matches(peerId: peerId, threadId: nil, generation: 1, input: movedCaret, state: state))
        let reply = ChatInterfaceState.ReplyMessageSubject(messageId: EngineMessage.Id(peerId: peerId, namespace: Namespaces.Message.Cloud, id: 9), quote: nil, innerSubject: nil)
        XCTAssertFalse(pending.matches(peerId: peerId, threadId: nil, generation: 1, input: input, state: state.withUpdatedReplyMessageSubject(reply)))
        XCTAssertFalse(pending.matches(peerId: peerId, threadId: nil, generation: 1, input: input, state: state.withUpdatedSendMessageEffect(42)))
        XCTAssertFalse(pending.matches(peerId: peerId, threadId: nil, generation: 1, input: input, state: state.withUpdatedComposeDisableUrlPreviews(["https://example.org"])))
        // Background persistence timestamps are not a different composer.
        XCTAssertTrue(pending.matches(peerId: peerId, threadId: nil, generation: 1, input: input, state: state.withUpdatedTimestamp(100)))
    }

    func testDelayedClearRebasesOnlyExplicitForumThreadTransfer() {
        let peerId = EnginePeer.Id(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1001))
        let otherPeerId = EnginePeer.Id(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1002))
        let input = self.fixture()
        let state = ChatInterfaceState().withUpdatedComposeInputState(input)
        let pending = ChatInputClearGuard(peerId: peerId, threadId: nil, generation: 1, input: input, state: state)
        XCTAssertFalse(pending.matches(peerId: peerId, threadId: 77, generation: 1, input: input, state: state))
        let routed = pending.withUpdatedThreadId(77)
        XCTAssertTrue(routed.matches(peerId: peerId, threadId: 77, generation: 1, input: input, state: state))
        XCTAssertFalse(routed.matches(peerId: otherPeerId, threadId: 77, generation: 1, input: input, state: state))
        XCTAssertFalse(routed.matches(peerId: peerId, threadId: 77, generation: 2, input: input, state: state))
    }

    func testPocketScopeRejectsPreviousVisitAfterReturningToTopic() {
        let peerId = EnginePeer.Id(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(1001))
        var scope = ChatInputScopeTracker()
        XCTAssertTrue(scope.update(peerId: peerId, threadId: 77))
        let firstVisit = scope.token
        XCTAssertTrue(scope.matches(firstVisit))
        // A routine observer refresh must not invalidate an operation in this visit.
        XCTAssertFalse(scope.update(peerId: peerId, threadId: 77))
        XCTAssertTrue(scope.matches(firstVisit))
        XCTAssertTrue(scope.update(peerId: peerId, threadId: 78))
        let secondTopic = scope.token
        XCTAssertFalse(scope.matches(firstVisit))
        XCTAssertTrue(scope.update(peerId: peerId, threadId: 77))
        // Peer/thread equality alone would accept both old observer and reply results.
        XCTAssertFalse(scope.matches(firstVisit))
        XCTAssertFalse(scope.matches(secondTopic))
        XCTAssertTrue(scope.matches(scope.token))
    }

    func testSameTextInAnotherWindowMustKeepItsEffectAndKeyboardLanguage() {
        let input = self.fixture()
        let captured = ChatInterfaceState().withUpdatedComposeInputState(input).withUpdatedInputLanguage("he").withUpdatedSendMessageEffect(42)
        XCTAssertTrue(captured.matchesDraftPocketComposer(captured, input: input, effectiveInputLanguage: "he"))
        // Equal text and reply metadata do not authorize overwriting another
        // window's independently selected effect or keyboard language.
        XCTAssertFalse(captured.withUpdatedSendMessageEffect(43).matchesDraftPocketComposer(captured, input: input, effectiveInputLanguage: "he"))
        XCTAssertFalse(captured.matchesDraftPocketComposer(captured, input: input, effectiveInputLanguage: "en"))
        XCTAssertFalse(captured.matchesDraftPocketComposer(captured, input: input, effectiveInputLanguage: nil))
        // The live keyboard language is authoritative over a stale saved mirror.
        XCTAssertTrue(captured.withUpdatedInputLanguage("en").matchesDraftPocketComposer(captured, input: input, effectiveInputLanguage: "he"))
    }

    func testPocketScopeDistinguishesPeerMainThreadAndUnsupportedContext() {
        let firstPeer = EnginePeer.Id(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(1001))
        let secondPeer = EnginePeer.Id(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(1002))
        var scope = ChatInputScopeTracker()
        XCTAssertFalse(scope.matches(nil))
        scope.update(peerId: firstPeer, threadId: nil)
        let mainChat = scope.token
        XCTAssertTrue(scope.update(peerId: firstPeer, threadId: 0))
        XCTAssertFalse(scope.matches(mainChat))
        let topic = scope.token
        XCTAssertTrue(scope.update(peerId: secondPeer, threadId: 0))
        XCTAssertFalse(scope.matches(topic))
        let otherPeer = scope.token
        XCTAssertTrue(scope.update(peerId: nil, threadId: nil))
        XCTAssertFalse(scope.matches(otherPeer))
    }
}
