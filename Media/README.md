# Capture evidence

No `demo.mp4`, poster, continuous take, master, or measured video artifact exists yet. These files are deliberately absent rather than replaced by mockups or empty media.

The desired final edit is approximately 36 seconds, 1920×1920, H.264 MP4, SDR Rec.709, yuv420p, no audio stream, faststart. Preserve the original native-resolution `simctl io recordVideo` take separately. Do not stretch the UI to square or call a 30→60 fps conversion a native 60 fps recording.

For every real capture, record the feature commit, upstream commit, patch SHA-256, exact installed executable hash, Xcode/iOS versions, simulator device/runtime, source resolution and frame timestamps. `ffprobe` timestamps and the pinned upstream `tools/animation_sheet.py` must be used to inspect motion frames and intervals. Nominal export fps does not prove smooth interaction.

Artifacts from an attempted CI run may contain only build/failure evidence. A successful artifact upload is not evidence that a simulator build or capture passed. See [TESTING](../docs/TESTING.md).
