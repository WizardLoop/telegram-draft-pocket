# Capture evidence

No completed `demo.mp4`, poster or master exists yet. Run [43329950073](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/43329950073) produced an original continuous recording of a failed launch: the app disappeared before its Pocket entrypoint appeared. This is failure evidence, not a working demo.

The original is preserved locally at `.local/evidence/macos-43329950073/unpacked/story/draft-pocket-continuous-raw.mov`, with SHA-256 `193a25a65ec37d34c0ee8ef74531c814dbffb4980aebf96159e742d69074304d`. Measured dimensions are 1206×2622; 279 frames span 432.798333 seconds by presentation timestamps. Only 25 adjacent frames changed, and the maximum timestamp gap is 213.196667 seconds. No 60 fps result is established. The unmodified ZIP and metadata are also retained locally; GitHub artifact `13999591921` has one-day retention. See [TESTING](../docs/TESTING.md) for provenance and failure details.

The desired final edit is approximately 36 seconds, 1920×1920, H.264 MP4, SDR Rec.709, yuv420p, no audio stream, faststart. Preserve the original native-resolution `simctl io recordVideo` take separately. Do not stretch the UI to square or call a 30→60 fps conversion a native 60 fps recording.

For every real capture, record the feature commit, upstream commit, patch SHA-256, exact installed executable hash, Xcode/iOS versions, simulator device/runtime, source resolution and frame timestamps. `ffprobe` timestamps and the pinned upstream `tools/animation_sheet.py` must be used to inspect motion frames and intervals. Nominal export fps does not prove smooth interaction.

Artifacts from an attempted CI run may contain only build/failure evidence. A successful artifact upload is not evidence that a simulator build or capture passed. See [TESTING](../docs/TESTING.md).
