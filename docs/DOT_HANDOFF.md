# Draft Pocket handoff

Status: implementation draft; no approved production capture.

- Repository: https://github.com/WizardLoop/telegram-draft-pocket
- Source branch: https://github.com/WizardLoop/telegram-draft-pocket/tree/feature/draft-pocket
- Draft PR: https://github.com/WizardLoop/telegram-draft-pocket/pull/1
- Target branch: `main`; do not merge until the reconstructed client builds and runs.
- Telegram base: `279f89e4d4f63f17227bf877dc3d8990f22ecd58`.
- Feature commit: use the immutable PR head SHA, not the mutable branch name.
- Patch hash: read `feature.patch.sha256` in `Integration/files.json`; record it again beside a future capture.
- Captures, continuous proof, poster and masters: **not produced**.
- Approved media: none supplied; `Demo/Assets/manifest.json` records the actual inventory.

Follow [integration](../Integration/README.md), [testing](TESTING.md), [CI](CI.md), and [demo](../Demo/README.md) instructions. Do not present a failed CI run's evidence archive as a working demo.

## Shot list for the future real capture

| Approximate edit time | Action |
|---|---|
| 0–6 s | Alex chat and long formatted draft, fictional context |
| 6–10 s | Incoming quick question, Put Aside through the actual control |
| 10–16 s | Short answer typed and sent through the real composer |
| 16–23 s | Pocket opens showing two separately saved cards |
| 23–31 s | Restore long draft, show preserved formatting, continue editing |
| 31–36 s | Send and settle; subtle WizardLoop credit in the edit |

Keep a separate uninterrupted take containing the whole interaction and QA takes of swap and restart. Main transitions should settle in approximately 220–320 ms without excessive overshoot; this remains a target, not a measurement. Reduce Motion should use a short fade. Inspect interruption cleanup, keyboard continuity, theme changes and no lingering snapshots.

Target delivery: approximately 36 s, 1920×1920 H.264 MP4, SDR Rec.709, yuv420p, no audio, faststart. Preserve native UI aspect ratio and original capture. Do not claim 60 fps until source frame timestamps and motion frames prove it. No frame interpolation can supply that evidence.

Do not contact Telegram Designers, publish a release, alter account entitlements, upload sticker packs or send to real people as part of this handoff. Large source captures belong in bounded artifact storage, not Git history or an unplanned LFS migration.
