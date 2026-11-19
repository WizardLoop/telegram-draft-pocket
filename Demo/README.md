# Full-client offline demo

The user authorized a **fictional `@wizardloop` demo**. This optional overlay opens that isolated fixture in the actual Telegram application: AppDelegate → Account/Postbox → SharedAccountContext/AccountContext → production ChatController. It does not use ComposerHarness, a replacement chat screen, a separate Pocket store, or a real Telegram account.

Latest [Mac run 44638214964](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/44638214964), at `630ab5a`, stopped during portable preflight before any app build or XCTest. No new UI result or movie was produced; its original ZIP/log were preserved automatically.

The last actual story, [run 43986695173](https://github.com/WizardLoop/telegram-draft-pocket/actions/runs/43986695173), at `7321a967`, built the full app in 3,960.580 s and XCUITest target in 88.613 s. The story launched, typed the reminder, opened Pocket and tapped the enabled first Put Aside button. The 15-second disappearance wait failed at XCTest line 48; no long draft, send, restore or QA followed. Nineteen phases reached `chatRouteRequested`, including `metalReady`; the operation failure has no established cause. Its artifact expired without a preserved ZIP or movie, while the decoded job log remains. Earlier `a940306` supplied a retained failed-launch movie, not a successful demo. See [TESTING](../docs/TESTING.md) and [Media](../Media/README.md).

The follow-up candidate adds 38 fixed operation phases to the existing 21 launch phases and captures the first XCTest issue as `demo-scenario-failure`. The vocabulary covers handler, preflight, snapshot, registry, fixed store result and dismissal; only enums and timestamps are written. The journal retains its latest 64 records, so earlier launch phases can roll out during long QA. These changes preserve production control flow and are diagnostic candidates, not a proven Put Aside remedy; the latest Mac preflight stopped before these diagnostics could run.

## Isolation and launch

Apply the feature first, then `Integration/demo.patch` and the files mapped from `Demo/Overlay`. The source is gated by `DEBUG && targetEnvironment(simulator)` and `--draft-pocket-demo`; production Draft Pocket works without this optional overlay or flag.

```sh
bash scripts/apply-feature.sh /path/to/telegram-ios --with-demo
bash Demo/run-demo.sh --app /path/to/Telegram.app --namespace manual-v1
# Optional later relaunch on the same launcher-created device:
bash Demo/run-demo.sh --udid RECORDED_SIMULATOR_UDID --app /path/to/Telegram.app --namespace manual-v1
```

The wrapper creates a new dedicated simulator by default and records its identity under ignored `.local/demo-simulators/`. An optional `--udid` is accepted only when the saved UUID, device name, runtime and app bundle identifier match the available dedicated simulator. Missing/mismatched identity is rejected. No device is erased and no app is uninstalled. The wrapper rejects a build without the embedded `DRAFT_POCKET_ISOLATED_DEMO_V1` marker before changing any simulator. The marker exists only in debug simulator executable code and is printed when the fixture branch starts. The automated XCTest similarly uses its own explicitly created device.

Supported app arguments:

- `--draft-pocket-demo`: enable the isolated full-client fixture.
- `--draft-pocket-demo-data NAME`: choose a persistent namespace; 1–64 ASCII letters, digits, underscores, or hyphens. Default `manual-v1`.
- `--draft-pocket-demo-secondary`: open a separate fictional Casey chat in the same account for isolation QA.

The data root is `<app-group>/Library/draft-pocket-demo-NAME`. AccountManager, encrypted Postbox, TempBox, logs, and shared/legacy context paths use this root. Relaunch reuses it; no launch deletes a database. Choose a new name for clean data. There is no automatic reset or wipe. The existing `--ui-test` account/login mode is not used and is mutually exclusive with this fixture.

RPC sessions are inert before account creation. Independent MTContext connections use a closed loopback SOCKS endpoint; backup-IP/iCloud discovery, initial WEB proxies, proxy changes, and engine changes cannot enable a real transport. APNs/VoIP registration and the startup background session are skipped. The native connection status remains offline. Network inspection during the first simulator run is still required; this is not a claim of a process-wide firewall covering arbitrary user-opened websites.

The account bootstrap verifies its actual root path before writing. It never imports credentials, OTPs, account sessions, auth keys, phone numbers, premium entitlements, or custom-emoji document IDs. The fictional peer has no real username resolution; `@wizardloop` is its display name. Its zero placeholder access hash exists only so the normal local send queue can construct a request for the inert engine. No request is transmitted or acknowledged.

## Real UI automation and capture

Native target: `//Tests/DraftPocketDemo:DraftPocketDemoUITests`, hosted by `//Telegram:Telegram`, with an iPhone 17 / iOS 27.0 runner and large test timeout. Use the repository's Make.py workflow and the owner's build configuration. The CI capture driver supplies an explicitly created dedicated simulator; no globally shared test simulator is reused.

The driver creates that device without booting, then calls optional Make `test --scriptPath ABS` to emit Bazel 9.2's official script for the exact target, filter and uppercase UUID. The `7321a967` Mac run reached UI interaction through this flow, but private scripts under the checkout then blocked strict native-stage reconstruction. The follow-up candidate puts scripts in an owned private temporary directory physically outside both checkout and artifacts, removing only registered scripts/markers after extraction. Environment, runfiles and fresh native output identity remain verified. After scoped build-server shutdown, the driver boots, probes a nonblank framebuffer, records and executes with a 600-second owned-group deadline; no Bazel invocation follows boot. Only typed cosmetic status-bar command failure is optional. Native results, binary identity and story gates remain mandatory, and later native work requires confirmed device release and clean runner supervision.

`testCaptureProductionStory` performs actual text entry and formatting-menu gestures, saves a second card through Put Aside, types the long sticker-review draft, waits for the incoming question, applies bold, puts the draft aside, enters and sends the short answer, restores the long draft, edits at its restored caret, and presses the real Send control. It attaches before/after screenshots for visual review. A demo-only observer sees the canonical typed content and inserts the question through a real Postbox transaction once the complete long draft exists. The fixed incoming message ID prevents duplication across relaunches. It never changes composer or Pocket state. XCTest requires the question's actual accessibility element and unchanged composer text before continuing.

**Send means local enqueue only.** The normal pending bubble is allowed to remain pending; no success RPC, delivery ticks, or server receipt is fabricated. The initial fixture history states that no message is delivered to an account. Retain that disclosure in the final video and handoff.

`testQASwapDeleteUndoRestartAndChatIsolation` performs nonempty-composer swap, native swipe Delete and the actual Undo overlay button, relaunch persistence, and a second-chat isolation check. It launches the same persistent namespace for relaunches and uses a fresh UUID namespace for each new test. Pocket cards are never seeded, and test code never imports or calls store/engine mutation helpers.

Only supported XCTest APIs drive the UI. The optional patch adds accessibility metadata to the existing Send and Undo buttons; their action handlers are unchanged. No private touch injector or pre-rendered success sequence is included. These APIs are described in [Apple's XCUIElement documentation](https://developer.apple.com/documentation/xcuiautomation/xcuielement).

Record the simulator's native screen continuously through the actual test run, including failures, and retain the raw take with the test result. Formatting screenshots and animation frames require human/agent visual inspection; a text-value assertion cannot establish font weight or animation quality. Follow the upstream animation-frame instructions before claiming timing, clipping, or theme behavior passed.

## Limits and first-run checks

- Build, startup, accessibility selectors, gestures, local enqueue, all assertions, network isolation, and recording must pass on the actual Mac runner before this is called a working demo.
- The app retains its normal app-group/passcode setup. An unsigned simulator build may require valid simulator entitlements; the overlay does not bypass authentication or keychain checks. Diagnose actual launch evidence before changing that setup.
- No user media was supplied. No custom emoji or media fixture is claimed. Production media-retention limits still apply.
- Dark mode, RTL, Dynamic Type, VoiceOver, Reduce Motion, reply targets, rich structural selection, and media need their documented additional QA.
- No real Telegram message is sent. API build configuration is not an account login. Never put real login/session data in source, CI secrets, logs, or artifacts.

See [DOT_HANDOFF](../docs/DOT_HANDOFF.md) for capture acceptance criteria and evidence requirements.
