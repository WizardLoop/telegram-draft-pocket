# Durable media work remaining

This is a design handoff, not implemented functionality. Keep `.mediaRetentionUnavailable` until the byte lifecycle is implemented and tested. Simply removing that guard would expose drafts to cache eviction.

At the pinned base, `MediaBox.keepResource` is an in-memory owner. `TimeBasedCleanup` does not make it durable. StorageBox references account for message/cache usage and are not an appropriate way to invent non-evictable Pocket ownership.

The proposed narrow solution is a retained-resource layer inside the existing account's MediaBox, with metadata in Postbox preferences. A dedicated `media/draft-pocket-retained/` directory survives today's nonrecursive root-size scan, but needs an explicit exclusion and regression tests. Resource filenames must use generated identifiers; no message text or user filenames belong in them.

Before a transfer, copy and verify every complete required resource into retained storage. Then commit durable owners and the pocket/active-state change in one Postbox transaction. Filesystem staging and SQLite cannot share a transaction: staging must be durable and reconciliation idempotent, with multiprocess-safe orphan handling. An unsuccessful preparation leaves the composer untouched.

Owners must cover pocket items, pending Undo tombstones, active chat/topic drafts and pending messages. Restore transfers ownership to the active draft. Enqueue claims a pending-message owner in the same transaction that adds the pending message; a failed/retryable send keeps it. Release bytes only after the last real owner disappears. Pocket ownership must never create an upload subscriber.

Relevant pinned source seams are `Postbox/Sources/MediaBox.swift`, `TimeBasedCleanup.swift`, `StorageBox/StorageBox.swift`, `TelegramCore/Sources/Account/Account.swift`, `PendingMessages/EnqueueMessage.swift` and `State/PendingMessageManager.swift`. Recovery must precede pending/active uploads. Normal send's enqueue-before-clear ordering must remain intact.

The retained archive alone does not protect the original MediaBox path while an upload reads it. Cache eviction, explicit clearing and retention changes need coordinated protection or a reliable read-time restoration path. Traversal must include all local representations, previews, video covers, alternatives, nested media and local custom-emoji resources, not just a principal file or thumbnail.

Native acceptance tests must use actual bytes: restart; age/size eviction; explicit cache clear; shared resource ownership; swap at capacity; Delete/Undo/finalize; interrupted prepare/commit; restore followed by failed send; successful-send release; and account isolation. None of these media-lifecycle tests has passed for this draft.
