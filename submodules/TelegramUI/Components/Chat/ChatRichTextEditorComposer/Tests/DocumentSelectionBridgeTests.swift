import XCTest
import Foundation
import TelegramCore
import RichTextEditorCore
@testable import ChatRichTextEditorComposer

final class DocumentSelectionBridgeTests: XCTestCase {
    func testZeroLengthFormulaProjectionCannotClaimExactAfterAtomCaret() {
        var formula = CharacterAttributes.plain
        formula.formula = ""
        let document = Document(blocks: [.paragraph(ParagraphBlock(id: BlockID("body"), runs: [TextRun(text: "\u{fffc}", attributes: formula)]))])
        let content = chatInputContent(fromDocument: document, resolveEmoji: { _ in nil }, resolveMedia: { _ in nil })
        let after = DocumentTextPosition(ref: .paragraph(BlockID("body")), offset: 1)
        XCTAssertNil(chatInputSelection(from: DocumentTextSelection(start: after, end: after), document: document, content: content))
    }

    func testEmojiAndFormulaBoundariesMapAcrossDocumentRebuild() throws {
        var emoji = CharacterAttributes.plain
        emoji.emoji = EmojiRef(id: "123", instanceID: "occurrence", altText: "👍🏽")
        var formula = CharacterAttributes.plain
        formula.formula = "a^2"
        let original = Document(blocks: [.paragraph(ParagraphBlock(id: BlockID("body"), runs: [
            TextRun(text: "A"), TextRun(text: "\u{fffc}", attributes: emoji),
            TextRun(text: "\u{fffc}", attributes: formula), TextRun(text: "Z")]))])
        let content = chatInputContent(fromDocument: original, resolveEmoji: { _ in (123, nil) }, resolveMedia: { _ in nil })
        let caret = DocumentTextPosition(ref: .paragraph(BlockID("body")), offset: 3)
        let captured = try XCTUnwrap(chatInputSelection(from: DocumentTextSelection(start: caret, end: caret), document: original, content: content))
        XCTAssertEqual(captured.start.offset, ("A👍🏽a^2" as NSString).length)
        let rebuilt = document(fromChatInputContent: content, registerEmoji: { id, _ in EmojiRef(id: String(id), instanceID: "new", altText: nil) }, registerMedia: { _ in "" })
        let restored = try XCTUnwrap(documentTextSelection(from: captured, document: rebuilt, content: content))
        XCTAssertNotEqual(restored.start.ref, caret.ref, "rebuild must not reuse the old ephemeral block identity")
        XCTAssertEqual(restored.start.offset, 3)
        var invalid = captured
        invalid.start.offset = 2 // inside the emoji's expanded alt text
        XCTAssertNil(documentTextSelection(from: invalid, document: rebuilt, content: content))
    }

    func testNestedQuoteProjectionPreservesOrdinaryFlatConsumers() {
        func paragraph(_ text: String) -> ChatInputBlock { .paragraph(ChatInputParagraph(runs: [ChatInputRun(text: text)])) }
        let content = ChatInputContent(blocks: [paragraph("p"), .blockQuote(ChatInputBlockQuote(content: ChatInputContent(blocks: [
            paragraph("q"), .blockQuote(ChatInputBlockQuote(content: ChatInputContent(blocks: [paragraph("abc")]), collapsed: false))]), collapsed: false)), paragraph("end")])
        let position = ChatInputPosition(path: [.init(blockIndex: 1), .init(blockIndex: 1), .init(blockIndex: 0)], offset: 2)
        XCTAssertEqual(content.flatOffset(for: position), ("p\nq\nab" as NSString).length)
        let author = ChatInputPosition(path: [.init(blockIndex: 1, slot: 1)], offset: 0)
        XCTAssertEqual(content.flatOffset(for: author), (content.plainText as NSString).length)
    }

    func testOffAxisCaretProjectsToEndAndInvalidTablePathCannotUseFlatFallback() {
        let content = ChatInputContent(blocks: [.table(ChatInputTable(rows: [ChatInputTableRow(cells: [ChatInputTableCell(runs: [ChatInputRun(text: "cell")])])])),
            .paragraph(ChatInputParagraph(runs: [ChatInputRun(text: "after")]))])
        let position = ChatInputPosition(path: [.init(blockIndex: 0), .init(blockIndex: 0)], offset: 2)
        XCTAssertEqual(content.flatOffset(for: position), 5)
        let invalid = ChatInputPosition(path: [.init(blockIndex: 0), .init(blockIndex: 9)], offset: 2)
        let selection = ChatInputSelection(start: invalid, end: invalid)
        XCTAssertFalse(isLegacyFlatInputSelection(selection, content: content))
        let rebuilt = document(fromChatInputContent: content, registerEmoji: { id, _ in EmojiRef(id: String(id), instanceID: "", altText: nil) }, registerMedia: { _ in "" })
        XCTAssertNil(documentTextSelection(from: selection, document: rebuilt, content: content))
    }
}
