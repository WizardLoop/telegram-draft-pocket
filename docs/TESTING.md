# Validation record

Date: 2026-11-04. Development host: Windows, Python 3.13, Git for Windows. No Swift compiler, macOS, Xcode or iOS Simulator was available locally.

| Layer | Actual status | Evidence / command |
|---|---|---|
| Packaging/configuration/capture regression suite | All 49 current cases exercised: 48 passed; 1 skipped (Windows cannot create symlinks). Full 33-case run took 439.586 s; 16 subsequently added cases passed separately | `python -m unittest discover -s Tests -v`; this was not one combined 49-case run |
| Python compilation / shell syntax | Passed for packaging scripts | `python -m py_compile scripts/package.py scripts/ci-simulator.py`; Bash `-n` |
| Upstream pin | Verified against official remote | `Integration/BASE.json` |
| Overlay and patch reconstruction | Updated package passed on a new clean worktree: all 32 feature file hashes equal development; separate demo's 3 new/4 modified files applied and verified; complete repeat application recognized | `bash scripts/check-integration.sh NEW_CLEAN_CHECKOUT --reference DEVELOPMENT_CHECKOUT`, then `bash scripts/apply-feature.sh NEW_CLEAN_CHECKOUT --with-demo` |
| Native feature tests | 53 cases authored: 13 core, 7 media lifecycle, 10 MediaBox bytes/cleanup, 6 interface, 10 actual composer-node, 4 structural bridge, 3 legacy UIKit facade; expanded cases not run | Five Bazel targets in `BASE.json` cover 50 cases; the additional 3 use upstream RichTextEditor's UIKit test suite |
| Reconstructed iOS client build | Not run locally | Requires pinned macOS/Xcode and owner configuration |
| Full-client UI / accessibility / animation | Not run | Requires rebuilt client on dedicated Simulator/device |
| Full-client offline demo and capture | Fixture and two real XCTest scenarios packaged; native run pending | Fictional `@wizardloop`, production UI/state, local enqueue only; no media artifact yet |
| GitHub Actions packaging | Passed on updated commit `4c5fb3deb31128627b1a8f2131bb7135400becdf`: all 28 tests passed (1.357 s), including symlink case; clean reconstruction and repeat-apply passed | [Run 41896273365](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/41896273365) |
| Expanded GitHub Actions packaging | Commit `1c3393c59a44f278031032153eede441dead3d72`: all 43 then-current tests passed (2.111 s), but actual clean reconstruction caught CRLF source/patch hash disagreement and failed before writes | [Run 41904879098](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/41904879098); source normalized, exporter regression added, local clean reconstruction passed afterward |
| Selection package on Linux | Passed on commit `9a453b78a76f545195e2240e9b766c46f21080cf`: all 49 tests passed in 1.887 s, with no skips; clean feature and demo reconstruction, both demo application paths and repeated application passed | [Run 41906571741](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/41906571741) |
| Current media package on Linux | Commit `6acc0131264ff3d54dbdd431078df9bb18a24a33` passed all 56 tests in 2.161 s; clean/repeated reconstruction verified all 45 feature files and the separate demo. The workflow-only update `7e86c9da486c7d661d36d311b08e3f269cb98611` also passed every check | [Media run 41913913519](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/41913913519), [current run 41914406257](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/41914406257) |
| First GitHub Actions macOS attempt | Earlier commit `4c5fb3deb31128627b1a8f2131bb7135400becdf`: full app built successfully (7,860 actions, 3,694.240 s); both then-existing native targets passed. Capture reached the old missing-fixture guard and failed. Overall run was cancelled following an intentional cancellation request | [Run 41896280828](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/41896280828), [redacted original evidence](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/41896280828/artifacts/13361805422), retained for one day |
| Selection package on macOS | Commit `9a453b78a76f545195e2240e9b766c46f21080cf`: full app built (7,860 actions, 3,535.165 s); core and interface targets passed. The composer harness failed compilation because the pinned harness lacked the current `makeShareScreen(context:params:)` protocol method. Later targets and capture did not run | [Failed run 41907370826](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/41907370826), [original evidence](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/41907370826/artifacts/13364354572), retained locally and for one day on GitHub |
| Current media package on macOS | Manual run on commit `7e86c9da486c7d661d36d311b08e3f269cb98611` is in progress; native media, full-client fixture and recording remain unverified | [Run 41914501467](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/41914501467); owner configuration supplied through Secrets |

Packaging tests use actual temporary Git repositories. They exercise base mismatch, changed sources, tampered hashes, patch preflight, collision rejection, path traversal, repeated application, incomplete state, unexpected files, export of untracked sources and paths containing spaces. The symlink case must run on Linux/macOS CI; a skipped case is not a pass.

The subsequent screenshot-evidence update adds seven portable cases: the current capture-only suite passed all 17 tests (2.589 s). The demo now inserts the fictional incoming question after actual long-draft typing, then asserts that the visible question did not change the composer. Its updated three-file overlay and five-file patch passed reconstruction on another clean pinned worktree. These changes still await native execution; run 41907370826 targets the preceding package.

Commit `9d1ef9522cdc9705c2bce1cfe8a53b4cdec9db9f` then passed all 56 portable tests in 2.186 s and every clean/repeated feature/demo reconstruction on Linux ([run 41912967699](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/41912967699)). The subsequently integrated media source expands the feature to 19 new and 26 modified files; its exact-base patch rehearsal and clean Linux reconstruction passed as recorded above. Native results are still pending. The 15 new media cases are authored tests, not recorded passes.

The first macOS artifact's stage report records build and native-test success separately from its failed capture. The two completed targets were `//submodules/TelegramCore:DraftPocketTests` (15.8 s) and `//submodules/ChatInterfaceState:DraftPocketComposerTests` (35.6 s). These logs establish target-level results, not individual test-method counts. The runner reported macOS 27.0.1 arm64, Xcode 27.1 (27A9275), image `20261026.0314.1`, available iOS 27.0/27.1 runtimes and 38,944,370,688 free bytes before building. A local copy of that original redacted artifact is preserved under ignored `.local/evidence/original-macos-41896280828/`. Those results apply only to the earlier source; they do not validate the new selection bridge or demo overlay.

The first Windows reconstruction caught global `core.autocrlf` conversion during scratch `git apply`. Preflight left the target clean. The corrected script uses an isolated scratch repository, explicitly disables line-ending conversion and strips inherited Git path overrides; the real reconstruction and two new regression cases then passed.

The expanded source export exposed a second line-ending edge on Linux: newly edited Windows files contained CRLF while Git's patch represented canonical LF. The export now rehearses its produced patch against exact base bytes before writing any package artifacts and rejects any source/result disagreement. The affected development files were normalized, re-exported and reconstructed successfully. A fresh Windows verification worktree also required repository-local `core.longpaths` and canonical LF integration inputs; no user checkout was reset or cleaned.

Native tests use the real `ChatInputContent`, `AdaptedPostboxEncoder/Decoder`, Postbox transactions and an on-disk reopen. They cover rich content, emoji metadata, reply/quote, structural selection, Put Aside, duplicate request rejection, Restore/swap, Delete/Undo, capacity, topic/peer/account isolation, stale active-state rejection, future-schema rejection, and safe media refusal. They do **not** prove media retention, UI, animation, process-kill crash recovery, storage hardware failures or live cloud-race behavior.

## Reproduction

The subsequent safety/evidence update contains 20 new and 27 modified feature files, plus a separate four-new/seven-modified demo. It adds three delayed-clear guard tests and two media lifecycle tests (normal autosave final-owner release and failed multi-resource preparation cleanup). These native additions remain unrun. Its 22 portable capture tests passed locally in 4.602 s, and seven independent CI-flow tests passed. The runner attachment patch was applied to the exact pinned source and verified to change only custom attachment retention. Full Linux reconstruction and the next native attempt must validate the combined update.

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
