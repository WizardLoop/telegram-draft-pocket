# Draft Pocket

Put an unfinished Telegram iOS message aside, answer a quick question, and return to the draft.

**Implementation draft.** The completed `d02b731` Mac run built the full app and passed all 54 hostless native cases, including media cold reopen. Three legacy UIKit selection cases passed separately. The hosted composer harness could not select its Simulator, and capture stopped at a status-bar timeout before recording. The newer database-error correction still needs native validation; no video exists. The source is on [`feature/draft-pocket`](https://github.com/WizardLoop/telegram-draft-pocket/tree/feature/draft-pocket) in [Draft PR #1](https://github.com/WizardLoop/telegram-draft-pocket/pull/1), pending review into `main`.

## Demo

No video or poster is published. [Capture status](Media/README.md) and [handoff](docs/DOT_HANDOFF.md) identify the missing evidence. This repository does not include a mock chat or fabricated capture.

## The interaction

You are writing a long formatted message when a quick question arrives. Open Draft Pocket, use **Put Aside**, send the short answer, then restore your original draft. If the composer already contains another draft, restoring swaps it into the pocket. Delete offers Undo. The optional demo uses a fictional `@wizardloop` conversation, with local enqueue only and no network delivery.

## Implemented source and current limits

- Account/peer/topic-local Postbox storage, versioned records, stable card IDs and a 20-card product limit without eviction.
- Atomic pocket/active-draft transfers with revision checks, safe swap, Delete/Undo and restart recovery of pending deletes.
- Canonical rich content, structural selection, custom-emoji file metadata, reply/quote and local composer-state persistence.
- Native composer entry and list, local English/Hebrew strings, accessibility labels, theme and Reduce Motion handling.
- Durable media archives, transactional owners for cards/Undo/active drafts/pending messages, and explicit recovery of interrupted composer sends; byte-retention and lifecycle cases passed at `d02b731`.

Media transfers require complete, verified resource bytes before the composer can be cleared. Missing or unsupported resources are refused. All 15 MediaBox and 10 Core media-lifecycle cases passed in the completed Mac run; this does not establish every upload, crash or interaction scenario. The latest SQL-first startup correction passed 114 portable checks and reconstruction on Linux, but has not run on a Mac. Hosted composer tests, the full-client story and visual QA remain outstanding. See [test evidence](docs/TESTING.md) and [limitations](docs/LIMITATIONS.md).

Pocket cards remain on this device; the active draft uses Telegram's existing synchronization. No server API or Pocket cloud sync is added. Secret Chats, sent-message editing, unfinished recordings and independent forms are excluded.

## Repository contents

`submodules/` contains only new native source/tests in their upstream target paths. `Integration/telegram-ios.patch` contains edits to existing upstream files. `Integration/BASE.json` and `files.json` pin the base and hashes. Scripts reconstruct and verify an independent local client checkout. The full Telegram client and its history are not included.

`Demo/Overlay` and `Integration/demo.patch` supply the separate debug Simulator fixture. The demo drives the same chat controller, Pocket transactions and UI as normal use. It never seeds Pocket cards or acknowledges fictitious network delivery.

## Quick start

Requires Git, Python 3.10+ and Bash (or invoke `python scripts/package.py` directly on Windows).

```sh
git clone --branch feature/draft-pocket https://github.com/WizardLoop/telegram-draft-pocket.git
cd telegram-draft-pocket
bash scripts/test.sh
bash scripts/bootstrap-telegram.sh "/path/to/new/telegram-ios"
bash scripts/check-integration.sh "/path/to/new/telegram-ios"
```

The base is **`279f89e4d4f63f17227bf877dc3d8990f22ecd58`**, Telegram iOS 13.1.2, Xcode 27.1, macOS 27, Bazel 9.2.0. Bootstrap refuses an existing destination; reconstruction preflights the entire package before editing. The check command applies the feature to that clean checkout and verifies every resulting hash.

## Build and test

For a Simulator build, bootstrap a separate checkout with `--submodules`, reconstruct it, and supply your own local app configuration:

```sh
bash scripts/build-simulator.sh "/path/to/new/telegram-ios" \
  --configuration "/private/path/local-configuration.json" \
  --cache "/path/to/bazel-cache" --build-number 1001
bash scripts/test.sh --native --checkout "/path/to/new/telegram-ios" \
  --configuration "/private/path/local-configuration.json" --cache "/path/to/bazel-cache"
```

This is not a credential-free one-command app build. Packaging checks can run on Windows/Linux; iOS validation requires the pinned Apple toolchain. [Manual GitHub Actions instructions](docs/CI.md) cover the compatible standard `xcode-27` runner, owner configuration, real failure artifacts and capture blockers. No paid Mac service is used.

The separate manual `editor` lane runs the three legacy UIKit selection cases without Telegram credentials or a full-client build. Its native results are reported independently from the five Bazel targets and the full-client demo.

## Documentation

- [Integration and export](Integration/README.md)
- [Actual test results and remaining QA](docs/TESTING.md)
- [Limitations](docs/LIMITATIONS.md)
- [CI experiment](docs/CI.md)
- [Demo preparation](Demo/README.md) and [Dot handoff](docs/DOT_HANDOFF.md)

## Credits and license

Draft Pocket by WizardLoop, integrated with Telegram iOS. Independent work; not an official Telegram feature. GPL-2.0-or-later, preserving upstream rights. See [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md). Presentation references inspired packaging/transparency; their code and media were not copied.
