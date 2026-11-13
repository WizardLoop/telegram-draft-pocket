#if canImport(UIKit)
import UIKit
import RichTextEditorCore
import RichTextInputDecUpstream

@available(iOS 18.4, *)
extension RichTextEngineEditorView {
    public var documentTextSelection: DocumentTextSelection? {
        guard tableStructuralSelection == nil, let nativeRange = engine.selectedTextRange else { return nil }
        let range = engineSelectedRange
        func position(at offset: Int, native: UITextPosition) -> DocumentTextPosition? {
            guard let index = roleIndex(atFlatOffset: offset), let ref = textNodeRef(ofRoleAt: index) else { return nil }
            let local = offset - flatOffset(ofRoleAt: index)
            guard local >= 0, local <= flatRange(ofRoleAt: index).length,
                  let modelOffset = modelOffset(fromEngineOffset: local, inRoleAt: index),
                  engineOffset(fromModelOffset: modelOffset, inRoleAt: index) == local else { return nil }
            guard let native = native as? IDRichTextPosition else { return nil }
            return DocumentTextPosition(ref: ref, offset: modelOffset,
                                        affinity: native.affinity == .backward ? .upstream : .downstream)
        }
        guard let start = position(at: range.location, native: nativeRange.start),
              let end = position(at: NSMaxRange(range), native: nativeRange.end) else { return nil }
        return DocumentTextSelection(start: start, end: end)
    }

    @discardableResult
    public func setDocumentTextSelection(_ selection: DocumentTextSelection) -> Bool {
        func resolve(_ position: DocumentTextPosition) -> (offset: Int, native: UITextPosition)? {
            guard let index = currentRoles.indices.first(where: { textNodeRef(ofRoleAt: $0) == position.ref }),
                  let runs = document.runs(for: position.ref), position.offset >= 0,
                  position.offset <= runs.reduce(0, { $0 + $1.utf16Count }),
                  let local = engineOffset(fromModelOffset: position.offset, inRoleAt: index),
                  modelOffset(fromEngineOffset: local, inRoleAt: index) == position.offset else { return nil }
            let offset = flatOffset(ofRoleAt: index) + local
            guard let base = engine.position(from: engine.beginningOfDocument, offset: offset) as? IDRichTextPosition else { return nil }
            guard let affinity = position.affinity else { return (offset, base) }
            let anchorDocument: IDBlockDocument? = base.anchor.document
            guard let document = anchorDocument else { return nil }
            // Build a live anchor through the upstream PUBLIC constructor. Its bias and the visual
            // affinity agree, just as the engine's positionForCanonicalOffset:affinity: constructs it.
            let anchor = IDRichTextAnchor(document: document, blockIdentifier: base.anchor.blockIdentifier,
                                          localOffset: base.anchor.localOffset, bias: affinity == .upstream ? .before : .after)
            return (offset, IDRichTextPosition(anchor: anchor, affinity: affinity == .upstream ? .backward : .forward))
        }
        guard let start = resolve(selection.start), let end = resolve(selection.end), start.offset <= end.offset,
              let nativeRange = engine.textRange(from: start.native, to: end.native) else { return false }
        let isCollapsed = start.offset == end.offset
        if isCollapsed, let startAffinity = selection.start.affinity, let endAffinity = selection.end.affinity,
           startAffinity != endAffinity {
            // One caret has one visual affinity, even when its two saved endpoints disagree.
            return false
        }
        let previousRange = engine.selectedTextRange
        let previousAffinity = engine.selectionAffinity
        let previousStructuralSelection = tableStructuralSelection
        tableStructuralSelection = nil
        engine.selectedTextRange = nativeRange
        if isCollapsed, let affinity = selection.start.affinity ?? selection.end.affinity {
            // UIKit's controller can retain its previous affinity when the offsets are unchanged.
            // Supplying positions with the desired affinity alone does not update that state.
            engine.selectionAffinity = affinity == .upstream ? .backward : .forward
        }
        func matches(_ actual: DocumentTextPosition, _ expected: DocumentTextPosition) -> Bool {
            return actual.ref == expected.ref && actual.offset == expected.offset
                && (expected.affinity == nil || actual.affinity == expected.affinity)
        }
        guard let actual = documentTextSelection,
              matches(actual.start, selection.start), matches(actual.end, selection.end) else {
            // A rejected restore must leave the current caret/selection intact.
            engine.selectedTextRange = previousRange
            engine.selectionAffinity = previousAffinity
            tableStructuralSelection = previousStructuralSelection
            return false
        }
        return true
    }
}
#endif
