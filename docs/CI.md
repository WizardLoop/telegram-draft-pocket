# CI and the macOS handoff

No remote build, native test, or recording is implied by committing these workflows. Actual results belong in `docs/TESTING.md` and the workflow's uploaded `status.json`.

## Hosted runner verified on 2026-11-04

The standard `xcode-27` Apple Silicon runner is listed in [GitHub's hosted-runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners). The published [runner image inventory](https://github.com/actions/runner-images/blob/main/images/macos/xcode-27-arm64-Readme.md), image `20261026.0314.1`, lists macOS 27.0.1, Xcode 27.1 (`27A9275`), and available iOS 27.0/27.1 runtimes. This matches the toolchain family locked in `Integration/BASE.json`; the job verifies its actual machine before doing a build. It never overrides the Xcode pin or silently substitutes another version.

GitHub lists the standard runner as 3 M1 CPUs, 7 GB RAM and 14 GB storage. These are documented runner specifications, not measurements of this project. A full Telegram build may exhaust memory, disk, or the 90-minute limit. The preflight records actual free disk space; a build failure remains a failure.

Standard hosted runners are free for public repositories according to the linked GitHub reference. This workflow refuses private repositories and never selects `-large`, `-xlarge`, or a paid/self-hosted runner. No workflow changes billing, repository visibility, Actions policy, secrets, or account settings. Artifact retention is one day. Account-specific artifact-storage allowances still need to be respected.

## Triggering a reviewed attempt

The macOS workflow is not triggered automatically by a push or ordinary PR update. A maintainer can explicitly apply `run-simulator` to a Draft PR whose head belongs to this same public repository. The `pull_request: labeled` event tests GitHub's PR merge ref, as described in [GitHub's event documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows). Evidence records the feature head SHA separately from the tested merge SHA.

`workflow_dispatch` is also defined, with a default-false usage acknowledgement. GitHub normally requires the workflow to exist on the default branch before manual dispatch is available. The label route allows the first reviewed attempt while implementation stays on `feature/draft-pocket`; do not push implementation to `main` or merge the PR just to expose the Run workflow button.

The separate `verify.yml` runs packaging regression tests and a clean reconstruction for public-repository PRs. It is explicitly named **not iOS**. A green packaging job proves neither compilation nor behavior in Telegram.

## Owner-provided configuration

The macOS attempt expects an already configured `DRAFT_POCKET_BUILD_CONFIGURATION_JSON` repository secret. Start with [`Integration/build-configuration.example.json`](../Integration/build-configuration.example.json), replace the two API placeholders with the owner's `api_id` and `api_hash` from [my.telegram.org/apps](https://my.telegram.org/apps), and save the complete JSON as that Actions secret. Do not paste those values into the PR, chat, repository, or logs. The validator checks every required field before invoking the upstream build system.

`org.wizardloop.draftpocket` is the proposed local bundle namespace. `DRAFTLOCAL` is a **Simulator-only namespace marker**, not an Apple Developer team or credential. With provisioning and Simulator signing disabled, the code uses it only to form the application identifier; the example disables Siri and iCloud. An owner with an actual team can use their own ten-character team identifier instead. No paid Apple membership, certificate or distribution profile is required by this unsigned Simulator configuration. Runtime keychain/app-group behavior with the local marker remains unverified until the client is actually built and run. Never use the marker for a device/distribution build. This repository supplies no Telegram API credentials or copied upstream secrets.

The job writes the configuration only to the ephemeral runner's temporary directory, removes it from child-process environment variables, masks sensitive fields and redacts captured logs. Configuration files, app binaries, archives, account databases and build caches are excluded from uploaded artifacts. Missing or malformed configuration stops the attempt with an explicit error. Creating a secret or changing repository settings is a separate owner action.

The Simulator build uses the pinned upstream `Make.py` and its full `Telegram/Telegram` target. A narrow integration patch exposes its existing `--disableProvisioningProfiles` capability to `build` and `test`, forwards the Telegram build setting and disables Simulator bundle/framework signing through the pinned rules_apple feature. Device builds reject this option. The wrapper also passes `--xcodeManagedCodesigning` to satisfy the upstream configuration resolver; it does not import a certificate or retrieve a profile. Distribution signing is not part of the workflow. A flag accepted by project generation alone was insufficient at this base.

## Evidence and current capture boundary

`status.json` distinguishes package integrity, toolchain, configuration, reconstruction, build, native tests and capture. A stage that did not run stays `not_run`; an interrupted stage can remain `running`. Redacted logs and toolchain metadata are uploaded with an `always()` step when the runner can still execute it. Failure before runner allocation or forced job termination can prevent artifact upload and must not be reported as a successful attempt.

There is currently no validated isolated full-client offline fixture or UI automation entry point. Consequently, the capture stage fails explicitly before opening an account or Simulator app. `Demo/run-demo.sh` is a fail-closed availability check, not a working demo launcher. It accepts the intended UDID/app arguments, returns nonzero when execution is unavailable, and never manufactures a recording. A successful build would still leave this capture requirement incomplete.

## Local Mac fallback

Only consider a local or separately authorized Mac after reviewing the hosted attempt. The required environment is Apple Silicon, macOS 27, Xcode 27.1, Python 3, Git, the exact pinned submodules and the owner's development configuration. A working provisioning/signing account is not required for an unsigned Simulator build.

For planning, 16 GB RAM and 100 GB free disk are conservative engineering suggestions, **not measured minimum requirements or a tested purchase recommendation**. The actual full-client build, native tests, runtime QA and capture must determine whether the available Mac is sufficient. Do not purchase or provision a paid machine automatically.

Use a new checkout and a dedicated Simulator. Reconstruct with `scripts/check-integration.sh`, then run `scripts/build-simulator.sh` and `scripts/test.sh --native` with the documented local configuration and cache arguments. Keep build numbers distinct and install only the latest verified `.ipa` payload; the upstream `Telegram_archive-root` directory can be stale. Once a real offline fixture exists, record a continuous take with `simctl io recordVideo`, inspect timestamped frames, and report measured frame pacing separately from the final export's nominal 60 fps.
