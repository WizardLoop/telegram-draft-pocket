import XCTest
import UIKit
import TelegramCore
import ChatInputTextNode
import ChatRichTextEditorComposer
import RichTextEditorCore
import RichTextInputDecUpstream
import RichTextEditorUIKit
@testable import ComposerHarnessLib
@testable import RichTextEngineEditor

/// Exercises the shipping node against its actual UITextInput, not a fabricated saved selection.
/// Pocket persistence is tested separately; this suite pins the capture/restore boundary it uses.
@available(iOS 18.4, *)
final class HarnessDraftPocketSelectionTests: XCTestCase {
    private func restored(_ content: ChatInputContent, selecting text: String, length: Int = 0,
                          _ check: (EngineFuzzDriver) throws -> Void) throws {
        let driver = try EngineFuzzDriver()
        defer { _ = driver.node.resignInputFirstResponder() }
        HarnessEdited.installFormulaRenderer(on: driver.host)
        driver.arrange(content)
        let location = (driver.viewText() as NSString).range(of: text).location
        XCTAssertNotEqual(location, NSNotFound)
        guard location != NSNotFound else { return }
        let nativeRange = NSRange(location: location, length: length)
        driver.setViewSelection(nativeRange)
        driver.settle(0.2)
        XCTAssertTrue(driver.node.supportsExactInputSelection)
        let captured = driver.node.currentInputContent()
        XCTAssertTrue(driver.node.canRestoreInputSelection(captured.selection, in: captured.content))

        _ = driver.node.resignInputFirstResponder()
        let empty = ChatInputContent(blocks: [])
        driver.node.setInputContent(empty, selection: ChatInputSelection(nsRange: NSRange(location: 0, length: 0), in: empty))
        XCTAssertTrue(driver.node.canRestoreInputSelection(captured.selection, in: captured.content), "an empty, unfocused field must still accept a saved selection")
        driver.node.restoreInputContent(captured.content, selection: captured.selection)
        _ = driver.node.makeInputFirstResponder()
        driver.settle(0.2)
        XCTAssertEqual(driver.viewSelection(), nativeRange, "the actual native caret/range moved during restore")
        XCTAssertEqual(driver.node.currentInputContent().selection, captured.selection)
        try check(driver)
    }

    func testDetailsBodyCaretSurvivesClearAndRestoreAndTypesInBody() throws {
        let content = ChatInputContent(blocks: [HarnessEngine.paragraph("before"),
            .details(ChatInputDetails(content: ChatInputContent(blocks: [HarnessEngine.paragraph("body target")]),
                                     title: [ChatInputRun(text: "summary")], expanded: true)),
            HarnessEngine.paragraph("after")])
        try restored(content, selecting: "target") { driver in
            XCTAssertEqual(driver.node.currentInputContent().selection.start.path.count, 2)
            driver.insert("X")
            driver.settle(0.2)
            guard case let .details(details) = driver.model().blocks[1] else { return XCTFail("missing details") }
            XCTAssertEqual(details.content.plainText, "body Xtarget")
        }
    }

    func testNestedTableCellRangeSurvivesRestoreAndReplacement() throws {
        let table = ChatInputBlock.table(ChatInputTable(columns: [.init(width: 120), .init(width: 120)], rows: [
            ChatInputTableRow(cells: [ChatInputTableCell(runs: [ChatInputRun(text: "left")]),
                                     ChatInputTableCell(runs: [ChatInputRun(text: "replace me")])])]))
        let content = ChatInputContent(blocks: [.details(ChatInputDetails(content: ChatInputContent(blocks: [table]),
            title: [ChatInputRun(text: "summary")], expanded: true)), HarnessEngine.paragraph("after")])
        try restored(content, selecting: "replace", length: 7) { driver in
            XCTAssertEqual(driver.node.currentInputContent().selection.start.path,
                           [.init(blockIndex: 0), .init(blockIndex: 0, slot: 0), .init(blockIndex: 1)])
            driver.insert("new")
            driver.settle(0.2)
            guard case let .details(details) = driver.model().blocks[0],
                  case let .table(table) = details.content.blocks[0] else { return XCTFail("missing nested table") }
            XCTAssertEqual(table.rows[0].cells[1].runs.map(\.text).joined(), "new me")
            XCTAssertEqual(table.rows[0].cells[0].runs.map(\.text).joined(), "left")
        }
    }

    func testCaretAfterRenderedFormulaAndEmojiSurvivesRestore() throws {
        let content = ChatInputContent(blocks: [HarnessEdited.paragraph([
            HarnessEdited.run("start "), HarnessEdited.formula("a^2"), HarnessEdited.emoji("👍🏽"), HarnessEdited.run("target")])])
        try restored(content, selecting: "target") { driver in
            XCTAssertEqual(driver.node.currentInputContent().selection.start.offset, ("start a^2👍🏽" as NSString).length)
            driver.insert("X")
            driver.settle(0.2)
            XCTAssertEqual(driver.model().plainText, "start a^2👍🏽Xtarget")
        }
    }

    func testDetailsTitleCaretUsesSecondaryRegion() throws {
        let content = ChatInputContent(blocks: [.details(ChatInputDetails(content: ChatInputContent(blocks: [HarnessEngine.paragraph("body")]),
            title: [ChatInputRun(text: "title target")], expanded: true))])
        try restored(content, selecting: "target") { driver in
            XCTAssertEqual(driver.node.currentInputContent().selection.start.path, [.init(blockIndex: 0, slot: 1)])
            driver.insert("X")
            driver.settle(0.2)
            guard case let .details(details) = driver.model().blocks[0] else { return XCTFail("missing details") }
            XCTAssertEqual(details.title.map(\.text).joined(), "title Xtarget")
            XCTAssertEqual(details.content.plainText, "body")
        }
    }

    func testFocusedEchoDoesNotMoveAnOffAxisCaret() throws {
        let driver = try EngineFuzzDriver()
        defer { _ = driver.node.resignInputFirstResponder() }
        driver.arrange(ChatInputContent(blocks: [.details(ChatInputDetails(content: ChatInputContent(blocks: [HarnessEngine.paragraph("body")]),
            title: [ChatInputRun(text: "title")], expanded: true))]))
        let title = (driver.viewText() as NSString).range(of: "title").location
        let body = (driver.viewText() as NSString).range(of: "body").location
        driver.setViewSelection(NSRange(location: title, length: 0))
        let old = driver.node.currentInputContent()
        driver.setViewSelection(NSRange(location: body, length: 0))
        driver.node.setInputContent(old.content, selection: old.selection)
        XCTAssertEqual(driver.viewSelection(), NSRange(location: body, length: 0))
    }

    func testStructuralTableSelectionIsExplicitlyUnsupported() throws {
        let driver = try EngineFuzzDriver()
        defer { _ = driver.node.resignInputFirstResponder() }
        driver.arrange(ChatInputContent(blocks: [.table(ChatInputTable(columns: [.init(width: 100)], rows: [
            ChatInputTableRow(cells: [ChatInputTableCell(runs: [ChatInputRun(text: "cell")])])]))]))
        guard case let .table(table) = driver.editor.document.blocks[0] else { return XCTFail("missing table") }
        driver.editor.tableStructuralSelection = (table.id, .rows(0 ... 0))
        XCTAssertFalse(driver.node.supportsExactInputSelection)
        XCTAssertNil(driver.editor.documentTextSelection)
    }

    func testExplicitSameBodyRestoreReplacesRangeWithSavedCaret() throws {
        let driver = try EngineFuzzDriver()
        defer { _ = driver.node.resignInputFirstResponder() }
        driver.arrange(ChatInputContent(blocks: [HarnessEngine.paragraph("abcdef")]))
        driver.setViewSelection(NSRange(location: 2, length: 0))
        let saved = driver.node.currentInputContent()
        driver.setViewSelection(NSRange(location: 1, length: 3))
        _ = driver.node.resignInputFirstResponder()
        // Ordinary echo keeps the range, while an explicitly committed Pocket restore must move it.
        driver.node.setInputContent(saved.content, selection: saved.selection)
        XCTAssertEqual(driver.viewSelection(), NSRange(location: 1, length: 3))
        driver.node.restoreInputContent(saved.content, selection: saved.selection)
        _ = driver.node.makeInputFirstResponder()
        driver.settle(0.2)
        XCTAssertEqual(driver.viewSelection(), NSRange(location: 2, length: 0))
        driver.insert("X")
        driver.settle(0.2)
        XCTAssertEqual(driver.model().plainText, "abXcdef")
    }

    func testReversedLegacyRangeIsRejectedBeforeEngineRestore() throws {
        let driver = try EngineFuzzDriver()
        defer { _ = driver.node.resignInputFirstResponder() }
        driver.arrange(ChatInputContent(blocks: [HarnessEngine.paragraph("abcdef")]))
        let content = driver.model()
        let reversed = ChatInputSelection(start: .init(path: [.init(blockIndex: 0)], offset: 4),
                                          end: .init(path: [.init(blockIndex: 0)], offset: 1))
        let before = driver.viewSelection()
        XCTAssertFalse(driver.node.canRestoreInputSelection(reversed, in: content))
        driver.node.restoreInputContent(content, selection: reversed)
        XCTAssertEqual(driver.viewSelection(), before)
    }

    func testVisualAffinitySurvivesActualNodeCaptureAndRestore() throws {
        let driver = try EngineFuzzDriver()
        defer { _ = driver.node.resignInputFirstResponder() }
        driver.arrange(ChatInputContent(blocks: [HarnessEngine.paragraph("abcdef")]))
        driver.setViewSelection(NSRange(location: 2, length: 0))
        var native = try XCTUnwrap(driver.editor.documentTextSelection)
        native.start.affinity = .upstream
        native.end.affinity = .upstream
        XCTAssertTrue(driver.editor.setDocumentTextSelection(native))
        let captured = driver.node.currentInputContent()
        XCTAssertEqual(captured.selection.start.affinity, .upstream)
        XCTAssertTrue(captured.selection.isCollapsed)
        _ = driver.node.resignInputFirstResponder()
        driver.node.setInputContent(ChatInputContent(blocks: [HarnessEngine.paragraph("other")]),
                                    selection: .init(nsRange: NSRange(location: 0, length: 0), in: ChatInputContent(blocks: [HarnessEngine.paragraph("other")])))
        driver.node.restoreInputContent(captured.content, selection: captured.selection)
        _ = driver.node.makeInputFirstResponder()
        driver.settle(0.2)
        let actual = try XCTUnwrap(driver.editor.engine.selectedTextRange?.start as? IDRichTextPosition)
        XCTAssertEqual(actual.affinity, .backward)
        XCTAssertEqual(driver.viewSelection(), NSRange(location: 2, length: 0))
    }

    func testLegacyRichNodeFocusedEchoUsesCanvasFirstResponder() throws {
        let driver = try EngineFuzzDriver()
        let previousWindow = driver.root.view.window
        let node = RichTextEditorChatInputNode(strings: driver.host.interfaceState.strings)
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 320, height: 600))
        window.addSubview(node.view)
        node.frame = window.bounds
        node.textFieldFrame = window.bounds
        window.makeKeyAndVisible()
        defer {
            _ = node.resignInputFirstResponder()
            window.isHidden = true
            previousWindow?.makeKeyAndVisible()
        }
        let content = ChatInputContent(blocks: [HarnessEngine.paragraph("abcdef")])
        node.setInputContent(content, selection: .init(nsRange: NSRange(location: 1, length: 0), in: content))
        node.layoutInputField()
        XCTAssertTrue(node.makeInputFirstResponder())
        let editor = try XCTUnwrap(node.inputView as? RichTextEditorView)
        XCTAssertTrue(editor.isEditorFirstResponder)
        editor.composerSelectedRange = NSRange(location: 1, length: 0)
        let old = node.currentInputContent()
        editor.composerSelectedRange = NSRange(location: 4, length: 0)
        node.setInputContent(old.content, selection: old.selection)
        XCTAssertEqual(editor.composerSelectedRange, NSRange(location: 4, length: 0))

        var unsupported = old.selection
        unsupported.start.affinity = .upstream
        unsupported.end.affinity = .upstream
        XCTAssertFalse(node.canRestoreInputSelection(unsupported, in: old.content))
    }
}
