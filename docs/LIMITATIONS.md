# Current limitations

This is an unverified implementation draft. Keep the pull request in Draft status. Windows packaging checks are not iOS build or runtime validation.

## Blocking gaps

- **Media:** Durable archives and transactional ownership are integrated, with native byte/lifecycle tests authored but not executed. This must not yet be described as verified media support. Only complete, readable image/file resources and their required representations can be prepared; unknown media kinds and missing bytes are refused before the transfer. Independent picker media, unfinished recordings and standalone forms remain unsupported. See [MEDIA_RETENTION](MEDIA_RETENTION.md) for the exact send coverage and remaining checks.
- **Runtime:** An earlier reconstructed client built and passed two native targets on the standard macOS runner. The expanded selection/demo/media source still needs its own native results. This local host is Windows without Xcode or Simulator; local source checks cannot establish runtime behavior.
- **Native selection:** The structural bridge now captures text endpoints and visual affinity in the actual rich editor, including table cells, details and secondary fields. Explicit committed transfers bypass the ordinary equal-content echo guard. Native regression tests are authored but unexecuted here. Structural table/atom/gap selections, reversed legacy-to-engine ranges, and affinity a destination backend cannot represent are refused before mutation; full selection support is not claimed.
- **Demo:** A full-client offline bootstrap and supported XCTest story/QA scenario are packaged separately. Native execution is still unverified. There is no actual video, poster, continuous proof take or measured fps yet. The composer harness tests do not replace the full-client recording.
- **Fixture identity:** The user selected a fictional `@wizardloop` conversation for the isolated demo. It does not access, authenticate as, or send to the real username. No account login/session material is packaged or transferred to Actions.
- **Interaction QA:** Light/Dark, RTL, VoiceOver, Dynamic Type, Reduce Motion, IME, external keyboard, iPad/window widths and live account/topic switching still require device/Simulator checks. Source-level handling is not a passing runtime test.

## Product boundaries

Pocket cards are local to one account, peer and optional thread. No Pocket cloud sync or server API is added. The active draft continues through the existing draft-sync mechanism. A product constant limits each pocket to 20 cards; there is no automatic time expiry or eviction. A pending Undo reserves its card's slot so a later Put Aside cannot make Undo destructive.

Secret Chats, editing a sent message, forwarding modes, incomplete recordings and unsupported composer modes are refused. These guards must be verified in the reconstructed client. A changed stored draft or stale revision rejects the operation. The UI temporarily freezes participating composer controls around an asynchronous commit and reconciles same-scope live controllers; cloud-draft races still require real integration QA.

The list uses plain text only for a short preview. Restore uses the canonical structural content, selection, full local interface state and custom-emoji file sidecar. The source does not invent server emoji IDs. Actual custom-emoji rendering and reply-target deletion fallback remain runtime acceptance tests.

No real account was logged into, no message was sent, no sticker pack was uploaded, and no release or store submission was made for this task.
