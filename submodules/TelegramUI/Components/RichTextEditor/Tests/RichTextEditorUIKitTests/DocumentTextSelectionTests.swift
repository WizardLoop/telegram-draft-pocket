#if canImport(UIKit)
import XCTest
import UIKit
import RichTextEditorCore
@testable import RichTextEditorUIKit

final class DocumentTextSelectionTests: XCTestCase {
    private func tableDocument() -> Document {
        let first = Cell(id: BlockID("cell-left"), blocks: [.paragraph(ParagraphBlock(id: BlockID("left"), runs: [TextRun(text: "left")]))])
        let second = Cell(id: BlockID("cell-right"), blocks: [.paragraph(ParagraphBlock(id: BlockID("right"), runs: [TextRun(text: "abcde")]))])
        let table = TableBlock(id: BlockID("table"), columns: [ColumnSpec(width: 100), ColumnSpec(width: 100)],
                               rows: [Row(id: BlockID("row"), cells: [first, second])])
        return Document(blocks: [.details(DetailsBlock(id: BlockID("details"), title: [TextRun(text: "summary")],
                                                       children: [.table(table)], expanded: true))])
    }

    func testNativeLegacyCellCaretRestoresAndTypesInTheSameCell() throws {
        let editor = RichTextEditorView(frame: CGRect(x: 0, y: 0, width: 320, height: 400))
        let document = tableDocument()
        editor.document = document
        let global = try XCTUnwrap(PositionResolver.globalPosition(of: .paragraph(BlockID("right")), offset: 2,
                                                                  in: DocumentTree.build(from: document)))
        editor.canvas.setSelectionForTesting(anchor: global, head: global)
        let captured = try XCTUnwrap(editor.documentTextSelection)
        XCTAssertEqual(captured.start, DocumentTextPosition(ref: .paragraph(BlockID("right")), offset: 2, affinity: .downstream))
        editor.document = Document(blocks: [.paragraph(ParagraphBlock(id: BlockID("other"), runs: [TextRun(text: "other")]))])
        editor.document = document
        XCTAssertTrue(editor.setDocumentTextSelection(captured))
        XCTAssertEqual(editor.canvas.canonicalSelection.head.utf16Offset, global)
        editor.canvas.insertText("X")
        XCTAssertEqual(editor.document.runs(for: .paragraph(BlockID("right")))?.map(\.text).joined(), "abXcde")
        XCTAssertEqual(editor.document.runs(for: .paragraph(BlockID("left")))?.map(\.text).joined(), "left")
    }

    func testReversedNativeLegacyRangeKeepsBothEndpoints() throws {
        let editor = RichTextEditorView(frame: CGRect(x: 0, y: 0, width: 320, height: 400))
        let document = tableDocument()
        editor.document = document
        let base = try XCTUnwrap(PositionResolver.globalPosition(of: .paragraph(BlockID("right")), offset: 0,
                                                                in: DocumentTree.build(from: document)))
        editor.canvas.setSelectionForTesting(anchor: base + 4, head: base + 1)
        let captured = try XCTUnwrap(editor.documentTextSelection)
        editor.document = document
        XCTAssertTrue(editor.setDocumentTextSelection(captured))
        XCTAssertEqual(editor.canvas.canonicalSelection.anchor.utf16Offset, base + 4)
        XCTAssertEqual(editor.canvas.canonicalSelection.head.utf16Offset, base + 1)
        editor.canvas.insertText("X")
        XCTAssertEqual(editor.document.runs(for: .paragraph(BlockID("right")))?.map(\.text).joined(), "aXe")
    }

    func testInvalidOffsetDoesNotMoveNativeSelection() throws {
        let editor = RichTextEditorView(frame: CGRect(x: 0, y: 0, width: 320, height: 400))
        editor.document = tableDocument()
        let before = editor.canvas.canonicalSelection
        let invalid = DocumentTextPosition(ref: .paragraph(BlockID("right")), offset: 100)
        XCTAssertFalse(editor.setDocumentTextSelection(DocumentTextSelection(start: invalid, end: invalid)))
        XCTAssertEqual(editor.canvas.canonicalSelection, before)
        editor.canvas.tableSelection = (BlockID("table"), .rows(0 ... 0))
        XCTAssertNil(editor.documentTextSelection)
    }
}
#endif
