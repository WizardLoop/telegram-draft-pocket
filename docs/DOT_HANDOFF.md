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

## Validation handoff, November 17

Latest `f6f634b` passed all 237 portable tests and clean/repeated reconstruction on [Linux run 43345189121](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/43345189121). [Mac run 43345627894](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/43345627894) built the full app in 4,130.182 s, the UI target in 77.958 s, and all five native targets. Capture failed before recording or XCTest at a 60-second status-bar timeout; owned cleanup also timed out. The job log shows 36 successful native cases, with 29 outcomes missing when the 110-minute step expired. The affinity correction has not completed its native regression.

Artifact `14007326835` uploaded 42 files/1,748,386 bytes but expired before download. No ZIP was preserved. The original decoded job log and metadata remain under `.local/evidence/macos-43345627894/`; log SHA-256 is `bb4b72c04407d50cd7aecc1825dc305eb9363db38cff44429672181266a958e5`. The older `a940306` 64/65 result and failed movie are separate preserved evidence. Three legacy UIKit cases passed separately; no full-client story passed.

The current candidate has not been dispatched to Mac. It emits official Bazel scripts for the exact target/filter/owned UUID before boot, validates their private paths and output identity, releases only the verified build server, then runs without invoking Bazel again. Per-target/take execution is bounded at 600 seconds with owned-group cleanup. Cosmetic status-bar command failure is recorded; framebuffer proof remains mandatory. Unconfirmed capture-device release or runner supervision blocks later native work. Require the new official-Bazel Linux emission probe to pass before dispatching Mac; its generic filtered/unfiltered cases are not iOS evidence. Fixed launch-phase enums are mirrored into logs. The workflow now allows a 150-minute step inside a 160-minute standard public `xcode-27` job; no paid runner is selected.

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
