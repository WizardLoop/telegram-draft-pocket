# Real-client demo status

No demo video has been recorded. The production overlay is implemented in Telegram's native composer and controller; no substitute chat screen is included. A full-client, isolated offline fixture is still required before an automated capture can be claimed.

The upstream ComposerHarness is useful for composer tests, but it has synthetic history and no `ChatControllerNode` keyboard machinery. It is **not** accepted here as proof of the requested full-client story. Existing upstream `--ui-test` account automation also uses network test accounts; it is not an offline Draft Pocket demo.

`run-demo.sh` validates its inputs and fails explicitly while this fixture is unavailable. It never opens a real account or silently records an unrelated screen. `apply-feature.sh --with-demo` likewise refuses unavailable demo integration. There is no supported reset command yet, because no demo data store has been created.

## Planned fixture and UI scenario

Create a disposable account/Postbox directory in a dedicated simulator and disable networking before the synthetic account opens. Seed only fictional Alex messages. The fixture must enter the real chat controller and the production store. Seed the additional pocket card by the same Put Aside path, not by a separate store implementation.

1. Compose: “I’ve reviewed the new sticker pack. The motion feels smoother, but the final highlight needs a softer transition. I’ll send the detailed notes shortly.” Apply subtle bold formatting.
2. Display Alex's fictional message: “Quick question — can we publish at 18:00?”
3. Use Put Aside through the real accessible control.
4. Type and send: “Yes — 18:00 works.” An offline fixture insertion must be identified as local simulation, not network delivery.
5. Open the pocket showing at least two cards. Restore the long draft, edit, and send. Check formatting survives.
6. Record separate QA takes of swap with a nonempty composer, relaunch, dark mode and RTL.

No user media was supplied. Do not invent custom-emoji server IDs or claim an ordinary Unicode emoji is a custom emoji. A valid custom-emoji fixture must be added and validated before including it in the capture.

Use XCUITest accessibility controls, screenshots and assertions. Do not inject private UIKit touch events, play back a pre-rendered video, or mutate the store in place of the visible interaction. See [DOT_HANDOFF](../docs/DOT_HANDOFF.md) for capture acceptance criteria.
