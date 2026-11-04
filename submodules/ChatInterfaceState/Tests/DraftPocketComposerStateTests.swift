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
}
