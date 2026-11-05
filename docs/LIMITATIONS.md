# Current limitations

This is an unverified implementation draft. Keep the pull request in Draft status. Windows packaging checks are not iOS build or runtime validation.

## Blocking gaps

- **Media:** The MediaBox byte-retention target passed on an earlier revision. Run `41992720504` passed the corrected corrupt-ownership case and failed-preparation cleanup, then failed cold reopen. Full media support is not verified. Only complete, readable image/file resources and their required representations can be prepared; unknown media kinds and missing bytes are refused before the transfer. Independent picker media, unfinished recordings and standalone forms remain unsupported. See [MEDIA_RETENTION](MEDIA_RETENTION.md) for the exact send coverage and remaining checks.
- **Runtime:** The expanded client built in run `41992720504`, but the native suite did not finish and the story Simulator timed out before UI execution. Earlier interface, selection-bridge and Postbox retention passes apply to their recorded revisions. This local host is Windows without Xcode or Simulator; local source checks cannot establish runtime behavior.
- **Native selection:** The structural bridge now captures text endpoints and visual affinity in the actual rich editor, including table cells, details and secondary fields. Explicit committed transfers bypass the ordinary equal-content echo guard. The bridge target passed; actual composer-node tests remain blocked by harness startup. Structural table/atom/gap selections, reversed legacy-to-engine ranges, and affinity a destination backend cannot represent are refused before mutation; full selection support is not claimed.
- **Demo:** A full-client offline bootstrap and supported XCTest story/QA scenario are packaged separately. Native execution is still unverified. There is no actual video, poster, continuous proof take or measured fps yet. The composer harness tests do not replace the full-client recording.
- **Fixture identity:** The user selected a fictional `@wizardloop` conversation for the isolated demo. It does not access, authenticate as, or send to the real username. No account login/session material is packaged or transferred to Actions.
- **Interaction QA:** Light/Dark, RTL, VoiceOver, Dynamic Type, Reduce Motion, IME, external keyboard, iPad/window widths and live account/topic switching still require device/Simulator checks. Source-level handling is not a passing runtime test.

## Product boundaries

Pocket cards are local to one account, peer and optional thread. No Pocket cloud sync or server API is added. The active draft continues through the existing draft-sync mechanism. A product constant limits each pocket to 20 cards; there is no automatic time expiry or eviction. A pending Undo reserves its card's slot so a later Put Aside cannot make Undo destructive.

Secret Chats, editing a sent message, forwarding modes, incomplete recordings and unsupported composer modes are refused. Draft Pocket is unavailable in the transient unsent new-topic composer; create or open the topic first. A closed/inaccessible topic or replaced input panel cannot consume a card through Restore. An already open Pocket remains readable, and a rejected transfer keeps its content. These guards must be verified in the reconstructed client.

A changed stored draft or stale revision rejects the operation. In-place topic switches rebind the Pocket and invalidate callbacks from earlier visits, including a return to the same topic. The UI temporarily freezes participating composer controls around an asynchronous commit and reconciles only matching input, reply, preview, effect and effective language fields. Automatic forum-send continuations must reach their requested destination before sending. Live navigation, permission changes and cloud-draft races still require real integration QA.

The list uses plain text only for a short preview. Restore uses the canonical structural content, selection, full local interface state and custom-emoji file sidecar. The source does not invent server emoji IDs. Actual custom-emoji rendering and reply-target deletion fallback remain runtime acceptance tests.

No real account was logged into, no message was sent, no sticker pack was uploaded, and no release or store submission was made for this task.
