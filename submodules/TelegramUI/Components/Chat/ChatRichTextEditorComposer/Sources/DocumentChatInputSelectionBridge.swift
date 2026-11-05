import Foundation
import TelegramCore
import RichTextEditorCore

/// Selection paths use slot 0 for body text / container children and slot 1 for the
/// secondary text (language, author, title). A table step's slot is its row index;
/// the following terminal step's blockIndex is the cell index within that row.
/// No ephemeral editor BlockID leaves this bridge. The existing flat selection API
/// retains its own contract and must not be used to decode these structural paths.
private struct DocumentSelectionEntry {
    let ref: TextNodeRef
    let path: [ChatInputPathStep]
    let runs: [TextRun]
    let chatText: String

    /// Maps atom boundaries exactly. An offset inside an expanded atom has no
    /// corresponding native caret and fails instead of being clamped elsewhere.
    func offset(_ offset: Int, toChat: Bool) -> Int? {
        guard offset >= 0 else { return nil }
        var projected = ""
        var segments: [(Int, Int)] = []
        for run in runs {
            let text: String
            if run.attributes.button != nil {
                text = "\u{fffc}"
            } else if let alt = run.attributes.emoji?.altText, !alt.isEmpty {
                text = alt
            } else if let formula = run.attributes.formula {
                text = formula
            } else {
                text = run.text
            }
            projected += text
            segments.append((run.utf16Count, (text as NSString).length))
        }
        guard projected == chatText else { return nil }
        var source = 0
        var target = 0
        for (documentLength, chatLength) in segments {
            let sourceLength = toChat ? documentLength : chatLength
            let targetLength = toChat ? chatLength : documentLength
            if offset == source { return target }
            if offset < source + sourceLength {
                return sourceLength == targetLength ? target + offset - source : nil
            }
            source += sourceLength
            target += targetLength
        }
        return offset == source ? target : nil
    }
}

private func documentSelectionEntries(document: Document, content: ChatInputContent) -> [DocumentSelectionEntry]? {
    var result: [DocumentSelectionEntry] = []
    func add(_ ref: TextNodeRef, _ path: [ChatInputPathStep], _ runs: [TextRun], _ chatRuns: [ChatInputRun]) {
        result.append(DocumentSelectionEntry(ref: ref, path: path, runs: runs, chatText: chatRuns.map(\.text).joined()))
    }
    func visit(_ blocks: [Block], _ chatBlocks: [ChatInputBlock], _ prefix: [ChatInputPathStep]) -> Bool {
        guard blocks.count == chatBlocks.count else { return false }
        for (index, pair) in zip(blocks, chatBlocks).enumerated() {
            let body = prefix + [ChatInputPathStep(blockIndex: index)]
            let secondary = prefix + [ChatInputPathStep(blockIndex: index, slot: 1)]
            switch pair {
            case let (.paragraph(a), .paragraph(b)):
                add(.paragraph(a.id), body, a.runs, b.runs)
            case let (.code(a), .code(b)):
                add(.code(a.id), body, a.runs, b.runs)
                add(.codeLanguage(a.id), secondary, [TextRun(text: a.language ?? "")], [ChatInputRun(text: b.language ?? "")])
            case let (.pullQuote(a), .pullQuote(b)):
                add(.pullQuote(a.id), body, a.runs, b.runs)
                add(.quoteAuthor(a.id), secondary, a.author, b.author)
            case let (.media(a), .media(b)):
                add(.caption(a.id), body, a.caption, b.caption)
            case let (.blockQuote(a), .blockQuote(b)):
                guard a.collapsed == b.collapsed else { return false }
                if !a.collapsed, !visit(a.children, b.content.blocks, body) { return false }
                add(.quoteAuthor(a.id), secondary, a.author, b.author)
            case let (.details(a), .details(b)):
                guard a.expanded == b.expanded else { return false }
                if a.expanded, !visit(a.children, b.content.blocks, body) { return false }
                add(.detailsTitle(a.id), secondary, a.title, b.title)
            case let (.table(a), .table(b)):
                guard a.rows.count == b.rows.count else { return false }
                for (rowIndex, rows) in zip(a.rows, b.rows).enumerated() {
                    guard rows.0.cells.count == rows.1.cells.count else { return false }
                    for (cellIndex, cells) in zip(rows.0.cells, rows.1.cells).enumerated() {
                        // ChatInput cells are inline-only. Refuse malformed editor cells
                        // that the content bridge would otherwise flatten during capture.
                        guard cells.0.blocks.count == 1, case let .paragraph(paragraph) = cells.0.blocks[0] else { return false }
                        let path = prefix + [ChatInputPathStep(blockIndex: index, slot: rowIndex), ChatInputPathStep(blockIndex: cellIndex)]
                        add(.paragraph(paragraph.id), path, paragraph.runs, cells.1.runs)
                    }
                }
            case (.buttonRow, .buttonRow):
                break
            default:
                return false
            }
        }
        return true
    }
    return visit(document.blocks, content.blocks, []) ? result : nil
}

func chatInputSelection(from selection: DocumentTextSelection, document: Document, content: ChatInputContent) -> ChatInputSelection? {
    guard let entries = documentSelectionEntries(document: document, content: content) else { return nil }
    func position(_ position: DocumentTextPosition) -> ChatInputPosition? {
        guard let entry = entries.first(where: { $0.ref == position.ref }),
              let offset = entry.offset(position.offset, toChat: true),
              entry.offset(offset, toChat: false) == position.offset else { return nil }
        let affinity: ChatInputPositionAffinity? = position.affinity.map { $0 == .upstream ? .upstream : .downstream }
        return ChatInputPosition(path: entry.path, offset: offset, affinity: affinity)
    }
    guard let start = position(selection.start), let end = position(selection.end) else { return nil }
    return ChatInputSelection(start: start, end: end)
}

func documentTextSelection(from selection: ChatInputSelection, document: Document, content: ChatInputContent) -> DocumentTextSelection? {
    guard let entries = documentSelectionEntries(document: document, content: content) else { return nil }
    // Before structural capture, an expanded quote used one top-level flat offset for all of its
    // children. Upgrade that old address explicitly; never pass table/secondary paths through NSRange.
    func upgraded(_ position: ChatInputPosition, in content: ChatInputContent) -> ChatInputPosition? {
        guard position.path.count == 1, let step = position.path.first, step.slot == 0,
              content.blocks.indices.contains(step.blockIndex),
              case let .blockQuote(quote) = content.blocks[step.blockIndex], !quote.collapsed,
              position.offset >= 0, position.offset <= (quote.content.plainText as NSString).length else { return nil }
        var child = quote.content.position(forFlatOffset: position.offset)
        if let nested = upgraded(child, in: quote.content) { child = nested }
        return ChatInputPosition(path: [step] + child.path, offset: child.offset, affinity: position.affinity)
    }
    func position(_ position: ChatInputPosition) -> DocumentTextPosition? {
        let position = upgraded(position, in: content) ?? position
        guard let entry = entries.first(where: { $0.path == position.path }),
              let offset = entry.offset(position.offset, toChat: false),
              entry.offset(offset, toChat: true) == position.offset else { return nil }
        let affinity: DocumentTextAffinity? = position.affinity.map { $0 == .upstream ? .upstream : .downstream }
        return DocumentTextPosition(ref: entry.ref, offset: offset, affinity: affinity)
    }
    guard let start = position(selection.start), let end = position(selection.end) else { return nil }
    return DocumentTextSelection(start: start, end: end)
}

/// Old drafts addressed an expanded quote by its flat offset at the top level.
/// Only that documented, on-axis representation may use the compatibility path.
/// A malformed structural/table position must never silently become an end caret.
func isLegacyFlatInputSelection(_ selection: ChatInputSelection, content: ChatInputContent) -> Bool {
    func valid(_ position: ChatInputPosition) -> Bool {
        guard position.path.count == 1, let step = position.path.first,
              step.slot == 0, position.offset >= 0 else { return false }
        if content.blocks.isEmpty { return step.blockIndex == 0 && position.offset == 0 }
        guard content.blocks.indices.contains(step.blockIndex) else { return false }
        guard content.blockIsFlatParticipating(content.blocks[step.blockIndex]) else { return false }
        var withoutAffinity = position
        withoutAffinity.affinity = nil
        return content.position(forFlatOffset: content.flatOffset(for: position)) == withoutAffinity
    }
    return valid(selection.start) && valid(selection.end)
}
