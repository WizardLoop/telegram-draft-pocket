# Durable media ownership

The implementation and native tests are integrated into the feature package. **They have not yet passed a native build or run.** Source inspection and the earlier text-only native results do not validate media retention.

The account's MediaBox stages immutable verified copies under `retained-draft-resources`. Generated/hash identifiers form the paths; message text and user filenames do not. Files and pin metadata are flushed before a transfer. A cross-process file lock covers preparation, the fresh Postbox owner transaction and post-commit collection. Failed preparation leaves the composer unchanged. Abandoned stages can be reclaimed only after consulting the current owner graph.

Postbox preferences contain the versioned owner graph and full interrupted-send recovery snapshots. Owners represent cards, Delete/Undo tombstones, active chat/topic drafts, prepared sends and real outgoing global message IDs. Pocket, active-state and ownership changes commit together. Autosave narrows active ownership when media is removed. Corrupt optional ownership metadata retains conservative pins without blocking ordinary text autosave or an unrelated send.

Age/size cleanup, explicit cache removal and destructive MediaBox moves respect durable pins. Writable cache paths stay unchanged. Complete/ranged reads can resolve the retained copy, including callbacks from existing file contexts. Startup verifies and restores originals before pending uploads; failed recovery pauses affected marked messages, while unrelated messages remain eligible. The Pocket exposes a recovery notice. Collection releases archives only after their final durable owner disappears; failed local messages retain ownership.

The canonical composer send path stores a recovery snapshot before clear-on-history hooks, Send When Online completion and automatic forum-topic transfer. A local-only attribute carries its operation through media transformation; the normal enqueue transaction claims pending-message ownership. A consumed operation cannot be reused as a new send. Retry must identify actual source messages and preserve them if replacement cannot be prepared. Unsupported mixed ephemeral/normal splits are refused before message mutation, leaving recovery intact. Pocket ownership creates no upload subscription or delivery acknowledgement.

Interrupted sends appear separately in the Pocket. Explicit recovery moves the snapshot to a card without overwriting current input and observes the 20-card limit. At capacity, recovery remains available until a slot is freed. Recovery entries are separately bounded and never silently evicted.

## Scope and remaining validation

- Traversal includes canonical nested media, file/image representations, video covers, alternatives, thumbnails and local custom-emoji files. Complete bytes are required; preparation does not download missing media.
- Pre-send protection covers `sendCurrentMessage`, including rich/expanded composer sends. Separate picker/sticker/bot-result actions retain upstream behavior. Some upstream clear-input callbacks are delayed and do not compare the original composer; interaction with restoring or typing newer content remains an explicit QA gap.
- Native tests use actual bytes, MediaBox reads/eviction and Postbox reopen/transactions: temporary local-file references after source deletion, force cleanup, age/size candidates, shared cards and swap at capacity, Delete/Undo/finalize, pre-send recovery, failed messages, explicit retry and final-owner release. Corrupt-metadata isolation and duplicate-send rejection are covered in source.
- The tests still need to run. Process-kill/fault injection, account switching with media, successful network-send migration, all representation variants, existing reader races and low-disk behavior require further acceptance evidence. No live account or network delivery is used in the fixture.

Relevant modules are Postbox's MediaBox/TimeBasedCleanup, TelegramCore's ownership/prepared-send helpers, Account/EnqueueMessage, canonical ChatInterfaceState autosave and the production composer UI. This integrates existing storage and upload paths; it does not replace them.
