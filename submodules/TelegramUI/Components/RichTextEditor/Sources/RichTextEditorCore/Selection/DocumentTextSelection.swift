import Foundation

public enum DocumentTextAffinity: Equatable {
    case upstream
    case downstream
}

/// A text endpoint in the current Document. Block identities are deliberately transient: a host
/// persisting this value must map them to its own structural paths before rebuilding the document.
public struct DocumentTextPosition: Equatable {
    public var ref: TextNodeRef
    public var offset: Int
    public var affinity: DocumentTextAffinity?

    public init(ref: TextNodeRef, offset: Int, affinity: DocumentTextAffinity? = nil) {
        self.ref = ref
        self.offset = offset
        self.affinity = affinity
    }
}

/// Text selections include collapsed carets and text inside cells/secondary regions. Structural
/// row/column/cell-rectangle selections and atom/gap selections are not text selections.
public struct DocumentTextSelection: Equatable {
    public var start: DocumentTextPosition
    public var end: DocumentTextPosition

    public init(start: DocumentTextPosition, end: DocumentTextPosition) {
        self.start = start
        self.end = end
    }
}
