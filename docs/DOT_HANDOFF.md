# Draft Pocket handoff

Status: implementation draft; no approved production capture.

The user selected a fictional `@wizardloop` conversation for the main demo. Keep the fixture visibly labelled offline and synthetic. It must run the production chat controller, composer and Pocket storage in a dedicated local account; it must not resolve the real username or connect to a real account.

- Repository: https://github.com/WizardLoop/telegram-draft-pocket
- Source branch: https://github.com/WizardLoop/telegram-draft-pocket/tree/feature/draft-pocket
- Draft PR: https://github.com/WizardLoop/telegram-draft-pocket/pull/1
- Target branch: `main`; do not merge until the reconstructed client builds and runs.
- Telegram base: `279f89e4d4f63f17227bf877dc3d8990f22ecd58`.
- Feature commit: use the immutable PR head SHA, not the mutable branch name.
- Patch hash: read `feature.patch.sha256` in `Integration/files.json`; record it again beside a future capture.
- Completed demo, continuous scenario proof, poster and masters: **not produced**. The original failed-launch recording from run `43329950073` is retained as diagnostic evidence; see [Media](../Media/README.md). It must not be presented as a working demo.
- Approved media: none supplied; `Demo/Assets/manifest.json` records the actual inventory.

Follow [integration](../Integration/README.md), [testing](TESTING.md), [CI](CI.md), and [demo](../Demo/README.md) instructions. Do not present a failed CI run's evidence archive as a working demo.

## Validation handoff, November 20

Latest `630ab5a` passed all 294 portable tests in 11.424 s, both actual Bazel emission probes and all reconstruction paths on [Linux run 44637744641](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/44637744641). [Mac run 44638214964](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/44638214964) then failed portable preflight: 294 cases in 24.382 s, one EOF/process-monitoring failure and two skips. Toolchain/configuration, app build, native tests and capture were not run. The narrow disk-guard correction has four passing new focused regressions and no Mac result.

The last actual UI run remains `7321a967` / `43986695173`: app and UI target built, the reminder was typed and the enabled first Put Aside was tapped, then its 15-second disappearance wait failed at line 48. Its operation cause is unproven, no later story/QA completed, and the affinity regression still has no successful retry.

The bounded local watcher preserved the latest original five-file ZIP, 57,162 bytes, SHA-256 `56e58e89dbb796613a065134e3ea64070b452c0893e2827af5f50405a1295e43`, and original job log, 81,309 bytes, SHA-256 `cfa2e70e612a45b795c426263be4f8ea58b749b1b8d6a252646a60b638587560`, under `.local/evidence/macos-44638214964/`. This is preflight failure evidence, with no movie. The older `7321a967` capture artifact expired without download; the `a940306` 64/65 native result and failed movie remain separate preserved evidence. Three legacy UIKit cases passed separately; no full-client story has passed.

The follow-up candidate moves private scripts outside both checkout and artifacts, preserves strict source checking, and adds a generic failure screenshot plus enum operation diagnostics without changing transfer guards or CAS. Its rolling journal retains the latest 64 enum/timestamp events; it is not a complete long-QA history or a proven Put Aside fix. No new Mac attempt is active. The existing runner emits official Bazel scripts before owned-device boot, validates exact identities, releases its build server and supervises each target/take for 600 seconds. Mandatory framebuffer, cleanup and strict 65-case gates remain; the workflow stays on the free standard public `xcode-27` runner with 150/160-minute step/job bounds. The bounded local watcher has now preserved a real failure archive and log; use one watcher per run. One-day retention is unchanged.

Before acceptance, require the current revision's strict 65-case native evidence, a successful story and QA take, installed-binary identity, original movie/frame timing and confirmed cleanup. Download each one-day artifact promptly. Do not infer an out-of-memory cause or a native pass from missing results.

## Shot list for the future real capture

| Approximate edit time | Action |
|---|---|
| 0–6 s | Fictional `@wizardloop` chat and long formatted draft |
| 6–10 s | Incoming quick question, Put Aside through the actual control |
| 10–16 s | Short answer typed and sent through the real composer |
| 16–23 s | Pocket opens showing two separately saved cards |
| 23–31 s | Restore long draft, show preserved formatting, continue editing |
| 31–36 s | Send and settle; subtle WizardLoop credit in the edit |

Keep a separate uninterrupted take containing the whole interaction and QA takes of swap and restart. Main transitions should settle in approximately 220–320 ms without excessive overshoot; this remains a target, not a measurement. Reduce Motion should use a short fade. Inspect interruption cleanup, keyboard continuity, theme changes and no lingering snapshots.

Target delivery: approximately 36 s, 1920×1920 H.264 MP4, SDR Rec.709, yuv420p, no audio, faststart. Preserve native UI aspect ratio and original capture. Do not claim 60 fps until source frame timestamps and motion frames prove it. No frame interpolation can supply that evidence.

Do not contact Telegram Designers, publish a release, alter account entitlements, upload sticker packs or send to real people as part of this handoff. Large source captures belong in bounded artifact storage, not Git history or an unplanned LFS migration.
