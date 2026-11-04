import Foundation

/// Local fallback translations until these keys are available in Telegram's language packs.
/// Server-supplied translations take precedence. No message content is used as a localization key.
public struct DraftPocketStrings {
    private let strings: PresentationStrings

    public init(_ strings: PresentationStrings) {
        self.strings = strings
    }

    private func value(_ key: String, _ english: String, _ hebrew: String) -> String {
        if let translated = self.strings.primaryComponent.dict["DraftPocket." + key] {
            return translated
        }
        return self.strings.baseLanguageCode.hasPrefix("he") ? hebrew : english
    }

    public var interruptedSends: String { self.value("InterruptedSends", "Interrupted sends", "שליחות שלא הושלמו") }
    public var recoverSend: String { self.value("RecoverSend", "Recover to Pocket", "שחזור לכיס הטיוטות") }
    public var sendNotPrepared: String { self.value("SendNotPrepared", "The media could not be preserved. Your message has not been sent and the draft is unchanged.", "לא ניתן לשמור את המדיה. ההודעה לא נשלחה והטיוטה לא השתנתה.") }
    public var sendChanged: String { self.value("SendChanged", "The draft changed while preparing to send. Your current text has been kept. Any saved recovery is in Draft Pocket.", "הטיוטה השתנתה בזמן ההכנה לשליחה. התוכן הנוכחי נשמר. אם נוצר עותק לשחזור, הוא זמין בכיס הטיוטות.") }

    public var title: String { self.value("Title", "Draft Pocket", "כיס טיוטות") }
    public var putAside: String { self.value("PutAside", "Put Aside", "שמירה בצד") }
    public var restore: String { self.value("Restore", "Restore", "החזרה לכתיבה") }
    public var delete: String { self.value("Delete", "Delete", "מחיקה") }
    public var empty: String { self.value("Empty", "Your pocket is empty", "הכיס שלך ריק") }
    public var localOnly: String { self.value("LocalOnly", "Saved on this device. Your active draft syncs as usual.", "נשמר במכשיר הזה. הטיוטה שבכתיבה מסתנכרנת כרגיל.") }
    public var reply: String { self.value("Reply", "Reply", "תגובה") }
    public var media: String { self.value("Media", "Media", "מדיה") }
    public var structured: String { self.value("Structured", "Rich content", "תוכן עשיר") }
    public var deleted: String { self.value("Deleted", "Draft deleted", "הטיוטה נמחקה") }
    public var unsupported: String { self.value("Unsupported", "Finish the current action before using Draft Pocket.", "יש לסיים את הפעולה הנוכחית לפני השימוש בכיס הטיוטות.") }
    public var failed: String { self.value("Failed", "The draft could not be moved. Your content has been kept.", "לא ניתן להעביר את הטיוטה. התוכן שלך נשמר.") }
    public var changed: String { self.value("Changed", "The draft changed. Please try again.", "הטיוטה השתנתה. אפשר לנסות שוב.") }
    public var full: String { self.value("Full", "This pocket has 20 drafts. Restore or delete one first.", "בכיס הזה יש 20 טיוטות. יש להחזיר לכתיבה או למחוק אחת מהן.") }
    public var undoDelete: String { self.value("UndoDelete", "Undo Delete", "ביטול מחיקה") }
    public var pendingDelete: String { self.value("PendingDelete", "Deleted drafts awaiting confirmation", "טיוטות שנמחקו וממתינות לאישור") }
    public var mediaUnavailable: String { self.value("MediaUnavailable", "The complete media could not be saved safely. Your draft is unchanged.", "לא ניתן לשמור בבטחה את המדיה המלאה. הטיוטה שלך לא השתנתה.") }
    public var mediaRecoveryUnavailable: String { self.value("MediaRecoveryUnavailable", "Some saved media could not be recovered. Affected messages are paused; their drafts have not been deleted.", "לא ניתן לשחזר חלק מהמדיה השמורה. ההודעות הנוגעות לה מושהות; הטיוטות שלהן לא נמחקו.") }
    public var storageUnavailable: String { self.value("StorageUnavailable", "Draft Pocket could not be read. Stored drafts have not been changed.", "לא ניתן לקרוא את כיס הטיוטות. הטיוטות השמורות לא השתנו.") }
    public var unavailableReply: String { self.value("UnavailableReply", "The original reply is unavailable. The draft text has been kept.", "ההודעה שאליה הגבת אינה זמינה. תוכן הטיוטה נשמר.") }
}
