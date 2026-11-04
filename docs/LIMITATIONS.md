# Current limitations

This is an unverified implementation draft. Keep the pull request in Draft status. Windows packaging checks are not iOS build or runtime validation.

## Blocking gaps

- **Media:** Put Aside/Restore rejects canonical content containing media before mutating storage. Telegram's existing `MediaBox.keepResource` is a live subscription, not durable ownership across restart and time-based cleanup. This version does not yet implement the required persistent byte retention and release lifecycle. It must not be described as full media support. Independent picker media, unfinished recordings and standalone forms are unsupported.
- **Runtime:** The reconstructed client has not been built or run locally; this host is Windows without Xcode or Simulator. Real native codec/transaction tests are authored but not locally executed.
- **Demo:** No isolated full-client offline bootstrap or validated UI automation scenario is packaged. There is no actual video, poster, continuous proof take or measured fps. The upstream composer-only harness is insufficient proof for this task.
- **Interaction QA:** Light/Dark, RTL, VoiceOver, Dynamic Type, Reduce Motion, IME, external keyboard, iPad/window widths and live account/topic switching still require device/Simulator checks. Source-level handling is not a passing runtime test.

## Product boundaries

Pocket cards are local to one account, peer and optional thread. No Pocket cloud sync or server API is added. The active draft continues through the existing draft-sync mechanism. A product constant limits each pocket to 20 cards; there is no automatic time expiry or eviction. A pending Undo reserves its card's slot so a later Put Aside cannot make Undo destructive.

Secret Chats, editing a sent message, forwarding modes, incomplete recordings and unsupported composer modes are refused. These guards must be verified in the reconstructed client. A changed stored draft or stale revision rejects the operation. The UI temporarily freezes participating composer controls around an asynchronous commit and reconciles same-scope live controllers; cloud-draft races still require real integration QA.

The list uses plain text only for a short preview. Restore uses the canonical structural content, selection, full local interface state and custom-emoji file sidecar. The source does not invent server emoji IDs. Actual custom-emoji rendering and reply-target deletion fallback remain runtime acceptance tests.

No real account was logged into, no message was sent, no sticker pack was uploaded, and no release or store submission was made for this task.
