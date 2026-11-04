# Third-party notices

Draft Pocket is an independent integration for Telegram iOS, not an official Telegram feature.

## Telegram iOS

Source: https://github.com/TelegramMessenger/Telegram-iOS

Pinned revision: `279f89e4d4f63f17227bf877dc3d8990f22ecd58`.

The integration patch modifies Telegram source. Telegram and its contributors retain their existing rights and notices. Telegram's [official applications page](https://telegram.org/apps) identifies the iOS source license as GNU GPL version 2 or later. This pinned tree does not contain a root LICENSE file; this package includes the GPL v2 text and distributes its additions under GPL-2.0-or-later. Individual upstream dependencies retain their own licenses; they are fetched at upstream's recorded commits, not redistributed in this repository.

## XCTest runner attachment retention

The optional demo includes a one-field patch to Google's [xctestrunner](https://github.com/google/xctestrunner/blob/4c6f4931aa65b3e6573b3ca11753c98fc8d3071c/xctestrunner/test_runner/xctestrun.py), pinned at `4c6f4931aa65b3e6573b3ca11753c98fc8d3071c` (Bazel version `0.2.16-20260212-4c6f493`). Copyright 2017 Google Inc., Apache License 2.0; the original [license text](docs/licenses/xctestrunner-Apache-2.0.txt) is included. The modification changes only UI tests' custom attachment lifetime from `keepNever` to `keepAlways`; it does not enable automatic screenshots or upgrade the runner. The original source header remains unchanged when applying the patch.

## Presentation references

- https://github.com/riveauxmia/telegram-unmute-similar
- https://github.com/riveauxmia/telegram-black-hole-delete
- https://github.com/niklavrukhin-cell/telegram-multi-reply
- https://t.me/designers

These were reviewed for packaging, presentation, and transparent reporting. No reference-project code or media is included. Their publication is not evidence that a feature was merged upstream.

## Assets

The only attachment supplied for this task was a text brief. No avatar, TGS, sticker, custom-emoji document, photo, video, or archive was supplied. No private attachment is redistributed. See `Demo/Assets/manifest.json`.

Original Draft Pocket additions and documentation: Copyright (c) 2026 WizardLoop, GPL-2.0-or-later.
