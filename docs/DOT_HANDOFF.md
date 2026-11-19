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

## Validation handoff, November 19

Latest `7321a967` passed all 285 portable tests in 10.513 s, actual generic Bazel filtered/unfiltered emission probes and all clean/repeated reconstruction paths on [Linux run 43986312350](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/43986312350); its evidence is preserved. [Mac run 43986695173](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/43986695173) built the full app in 3,960.580 s and UI target in 88.613 s. The story launched, typed the reminder and tapped the enabled first Put Aside button; its disappearance wait failed after 15 seconds at XCTest line 48, with 85.894 s total testcase time. Nineteen phases reached `chatRouteRequested`, including `metalReady`. No store/dismissal cause is proven. No native cases ran because private runner files contaminated the strict reconstructed-source inventory after capture. The affinity correction therefore remains untested.

Artifact `14289758795` uploaded 53 files/24,487,919 bytes, SHA-256 `57c95ded7fa25148c921008cc3fea6cf4182827b4f14d43db958f004b15a63c9`, then expired before download. No ZIP or new movie was preserved. The original decoded job log remains under `.local/evidence/macos-43986695173/`, 1,257,069 bytes, SHA-256 `55c20ffd3c166e4ab0f12310c0bf5a677c904236d8b58f8635cba07a20e36597`. The older `a940306` 64/65 native result and failed movie are separate preserved evidence. Three legacy UIKit cases passed separately; no full-client story has passed.

The follow-up candidate moves private scripts outside both checkout and artifacts, preserves strict source checking, and adds a generic failure screenshot plus enum operation diagnostics without changing transfer guards or CAS. Its rolling journal retains the latest 64 enum/timestamp events; it is not a complete long-QA history or a proven Put Aside fix. No new Mac attempt is active. The existing runner emits official Bazel scripts before owned-device boot, validates exact identities, releases its build server and supervises each target/take for 600 seconds. Mandatory framebuffer, cleanup and strict 65-case gates remain; the workflow stays on the free standard public `xcode-27` runner with 150/160-minute step/job bounds. A bounded local artifact-download watcher is being prepared, not activated; one-day retention is unchanged.

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
