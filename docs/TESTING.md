# Validation record

Date: 2026-11-04. Development host: Windows, Python 3.13, Git for Windows. No Swift compiler, macOS, Xcode or iOS Simulator was available locally.

| Layer | Actual status | Evidence / command |
|---|---|---|
| Packaging/configuration/capture regression suite | All 49 current cases exercised: 48 passed; 1 skipped (Windows cannot create symlinks). Full 33-case run took 439.586 s; 16 subsequently added cases passed separately | `python -m unittest discover -s Tests -v`; this was not one combined 49-case run |
| Python compilation / shell syntax | Passed for packaging scripts | `python -m py_compile scripts/package.py scripts/ci-simulator.py`; Bash `-n` |
| Upstream pin | Verified against official remote | `Integration/BASE.json` |
| Overlay and patch reconstruction | Updated package passed on a new clean worktree: all 32 feature file hashes equal development; separate demo's 3 new/4 modified files applied and verified; complete repeat application recognized | `bash scripts/check-integration.sh NEW_CLEAN_CHECKOUT --reference DEVELOPMENT_CHECKOUT`, then `bash scripts/apply-feature.sh NEW_CLEAN_CHECKOUT --with-demo` |
| Native feature tests | 33 cases authored: 13 core, 3 interface, 10 actual composer-node, 4 structural bridge, 3 legacy UIKit facade; not run locally | Four Bazel targets in `BASE.json` cover 30 cases; the additional 3 use upstream RichTextEditor's UIKit test suite |
| Reconstructed iOS client build | Not run locally | Requires pinned macOS/Xcode and owner configuration |
| Full-client UI / accessibility / animation | Not run | Requires rebuilt client on dedicated Simulator/device |
| Full-client offline demo and capture | Fixture and two real XCTest scenarios packaged; native run pending | Fictional `@wizardloop`, production UI/state, local enqueue only; no media artifact yet |
| GitHub Actions packaging | Passed on updated commit `4c5fb3deb31128627b1a8f2131bb7135400becdf`: all 28 tests passed (1.357 s), including symlink case; clean reconstruction and repeat-apply passed | [Run 41896273365](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/41896273365) |
| Expanded GitHub Actions packaging | Commit `1c3393c59a44f278031032153eede441dead3d72`: all 43 then-current tests passed (2.111 s), but actual clean reconstruction caught CRLF source/patch hash disagreement and failed before writes | [Run 41904879098](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/41904879098); source normalized, exporter regression added, local clean reconstruction passed afterward |
| GitHub Actions macOS | Actual manual attempt running on commit `4c5fb3deb31128627b1a8f2131bb7135400becdf` | [Run 41896280828](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/41896280828); owner configuration supplied through Secrets |

Packaging tests use actual temporary Git repositories. They exercise base mismatch, changed sources, tampered hashes, patch preflight, collision rejection, path traversal, repeated application, incomplete state, unexpected files, export of untracked sources and paths containing spaces. The symlink case must run on Linux/macOS CI; a skipped case is not a pass.

The first Windows reconstruction caught global `core.autocrlf` conversion during scratch `git apply`. Preflight left the target clean. The corrected script uses an isolated scratch repository, explicitly disables line-ending conversion and strips inherited Git path overrides; the real reconstruction and two new regression cases then passed.

The expanded source export exposed a second line-ending edge on Linux: newly edited Windows files contained CRLF while Git's patch represented canonical LF. The export now rehearses its produced patch against exact base bytes before writing any package artifacts and rejects any source/result disagreement. The affected development files were normalized, re-exported and reconstructed successfully. A fresh Windows verification worktree also required repository-local `core.longpaths` and canonical LF integration inputs; no user checkout was reset or cleaned.

Native tests use the real `ChatInputContent`, `AdaptedPostboxEncoder/Decoder`, Postbox transactions and an on-disk reopen. They cover rich content, emoji metadata, reply/quote, structural selection, Put Aside, duplicate request rejection, Restore/swap, Delete/Undo, capacity, topic/peer/account isolation, stale active-state rejection, future-schema rejection, and safe media refusal. They do **not** prove media retention, UI, animation, process-kill crash recovery, storage hardware failures or live cloud-race behavior.

## Reproduction

```sh
bash scripts/test.sh
bash scripts/bootstrap-telegram.sh "/path/to/new/telegram-ios" --submodules
bash scripts/check-integration.sh "/path/to/new/telegram-ios"
bash scripts/build-simulator.sh "/path/to/new/telegram-ios" \
  --configuration "/private/path/local-configuration.json" \
  --cache "/path/to/bazel-cache" --build-number 1001
bash scripts/test.sh --native --checkout "/path/to/new/telegram-ios" \
  --configuration "/private/path/local-configuration.json" --cache "/path/to/bazel-cache"
```

Use the owner's API/application identifiers. Never copy upstream signing credentials or another developer's simulator UUID. The helper rejects missing/mismatched toolchains and dependencies instead of overriding the pin. The native target uses an iPhone 17 / iOS 27.0 test runner available in the verified `xcode-27` image.

## Remaining acceptance checks

Run the full story with the legacy field, native rich editor, and engine editor where supported. Exercise mixed Hebrew/English, combining marks and complex emoji, selection within nested blocks, media refusal without content loss, deleted reply target, changed permissions, full pocket swap, repeated taps, close during commit, two chat controllers, incoming cloud draft, restart and account/topic switching. Confirm unsupported modes cannot clear content.

For animation, write expected movement/timing first, record with native `simctl`, inspect labelled timestamped frames using the pinned upstream `tools/animation_sheet.py`, and record gaps/overshoot/cleanup. Verify the installed executable hash against the fresh `.ipa`; do not trust stale archive-root folders. Preserve raw take and `ffprobe` frame timestamps. No 60 fps assertion may be based on the export setting alone.
