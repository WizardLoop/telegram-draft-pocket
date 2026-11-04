import XCTest

/// Drives the full Telegram application. No engine/store helpers are linked into this test.
final class DraftPocketDemoUITests: XCTestCase {
    private let longDraft = "I’ve reviewed the new sticker pack. The motion feels smoother, but the final highlight needs a softer transition. I’ll send the detailed notes shortly."

    private func event(_ name: String) {
        let value: [String: Any] = ["event": name, "utc": Date().timeIntervalSince1970]
        if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
           let text = String(data: data, encoding: .utf8) {
            print("DRAFT_POCKET_EVENT \(text)")
        } else {
            XCTFail("Could not encode capture timing marker")
        }
    }

    private func element(_ app: XCUIApplication, _ identifier: String) -> XCUIElement {
        return app.descendants(matching: .any).matching(identifier: identifier).firstMatch
    }

    private func editor(_ app: XCUIApplication) -> XCUIElement {
        let result = app.textViews.firstMatch
        XCTAssertTrue(result.waitForExistence(timeout: 20), "The production composer must expose an editable text view")
        return result
    }

    private func wait(_ expression: @escaping () -> Bool, file: StaticString = #filePath, line: UInt = #line) {
        let expectation = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in expression() }, object: nil)
        XCTAssertEqual(XCTWaiter.wait(for: [expectation], timeout: 15), .completed, file: file, line: line)
    }

    private func type(_ text: String, in app: XCUIApplication) {
        let input = self.editor(app)
        input.tap()
        input.typeText(text)
        self.wait { (input.value as? String)?.contains(text) == true }
    }

    private func putAside(_ app: XCUIApplication) {
        let open = self.element(app, "DraftPocket.Open")
        XCTAssertTrue(open.waitForExistence(timeout: 20))
        open.tap()
        let putAside = self.element(app, "DraftPocket.PutAside")
        XCTAssertTrue(putAside.waitForExistence(timeout: 10))
        self.wait { putAside.isEnabled }
        self.event("put_aside_start")
        putAside.tap()
        self.wait { !putAside.exists }
        RunLoop.current.run(until: Date().addingTimeInterval(0.6))
        self.event("put_aside_end")
    }

    private func restore(_ text: String, in app: XCUIApplication) {
        self.element(app, "DraftPocket.Open").tap()
        let card = app.descendants(matching: .any).matching(NSPredicate(format:
            "identifier BEGINSWITH %@ AND label CONTAINS %@", "DraftPocket.Restore.", text)).firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: 10))
        self.event("restore_start")
        card.tap()
        self.wait { (self.editor(app).value as? String)?.contains(text) == true }
        RunLoop.current.run(until: Date().addingTimeInterval(0.6))
        self.event("restore_end")
    }

    private func launch(namespace: String, secondary: Bool = false) -> XCUIApplication {
        self.continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--draft-pocket-demo", "--draft-pocket-demo-data", namespace, "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        if secondary { app.launchArguments.append("--draft-pocket-demo-secondary") }
        app.launch()
        XCTAssertTrue(self.element(app, "DraftPocket.Open").waitForExistence(timeout: 45))
        return app
    }

    private func snapshot(_ name: String, app: XCUIApplication) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        self.add(attachment)
    }

    private func label(_ label: String, app: XCUIApplication) -> XCUIElement {
        return app.descendants(matching: .any).matching(NSPredicate(format: "label == %@", label)).firstMatch
    }

    private func boldAllText(_ app: XCUIApplication) {
        let input = self.editor(app)
        // The same supported edit-menu gestures exercised by upstream HarnessEditMenuUITests.
        let point = input.coordinate(withNormalizedOffset: CGVector(dx: 0.2, dy: 0.25))
        point.press(forDuration: 0.1)
        RunLoop.current.run(until: Date().addingTimeInterval(0.6))
        point.press(forDuration: 0.1)
        let selectAll = self.label("Select All", app: app)
        XCTAssertTrue(selectAll.waitForExistence(timeout: 5))
        selectAll.tap()
        let format = self.label("Format", app: app)
        XCTAssertTrue(format.waitForExistence(timeout: 5))
        format.tap()
        let bold = self.label("Bold", app: app)
        XCTAssertTrue(bold.waitForExistence(timeout: 5))
        bold.tap()
        // Collapse the selection with an ordinary touch. iPhone capture must not depend on
        // macOS/iPad keyboard-only typeKey behavior or private key/touch injection.
        input.coordinate(withNormalizedOffset: CGVector(dx: 0.6, dy: 0.8)).tap()
        self.wait { (input.value as? String) == self.longDraft }
    }

    private func sendLocally(_ text: String, app: XCUIApplication) {
        let send = self.element(app, "DraftPocketDemo.Send")
        XCTAssertTrue(send.waitForExistence(timeout: 10))
        self.wait { send.isEnabled && send.isHittable }
        self.event("send_local_start")
        send.tap()
        self.wait {
            guard let value = self.editor(app).value as? String else { return false }
            return !value.contains(text)
        }
        self.event("send_local_end")
        // The fixture has no network acknowledgments. A queued bubble is not delivery evidence.
        self.snapshot("local-enqueue-only-no-network-delivery", app: app)
    }

    func testCaptureProductionStory() {
        self.event("story_start")
        let app = self.launch(namespace: UUID().uuidString)
        self.type("Remember to review the easing curve.", in: app)
        self.putAside(app)
        self.type(self.longDraft, in: app)
        let question = app.descendants(matching: .any).matching(NSPredicate(format:
            "label CONTAINS %@", "Quick question — can we publish at 18:00?")).firstMatch
        XCTAssertTrue(question.waitForExistence(timeout: 15), "The incoming fixture must arrive after actual draft typing")
        XCTAssertEqual(self.editor(app).value as? String, self.longDraft)
        self.event("incoming_question_visible")
        self.boldAllText(app)
        self.snapshot("formatted-composer-before-put-aside", app: app)
        self.putAside(app)

        self.type("Yes — 18:00 works.", in: app)
        self.sendLocally("Yes — 18:00 works.", app: app)
        self.restore("I’ve reviewed the new sticker pack.", in: app)
        self.wait { (self.editor(app).value as? String) == self.longDraft }
        self.snapshot("formatted-composer-restored-review-frames", app: app)
        // Edit at the exact caret restored by the production transfer. Verify that the insertion
        // preserves all prior text; its location is deliberately not reconstructed by the test.
        let addition = " (revised)"
        app.typeText(addition)
        self.wait { ((self.editor(app).value as? String) ?? "").replacingOccurrences(of: addition, with: "") == self.longDraft }
        let edited = self.editor(app).value as? String ?? ""
        XCTAssertTrue(edited.contains(addition))
        self.sendLocally(edited, app: app)
        self.event("story_end")
    }

    func testQASwapDeleteUndoRestartAndChatIsolation() {
        self.event("qa_start")
        // A fresh namespace is created, never an erase/reset of existing app or account data.
        let namespace = UUID().uuidString
        var app = self.launch(namespace: namespace)

        self.type("Remember to review the easing curve.", in: app)
        self.putAside(app)
        self.type(self.longDraft, in: app)
        self.putAside(app)

        // Restore while nonempty: the real production transfer must preserve this short draft.
        self.type("Yes — 18:00 works.", in: app)
        self.restore("I’ve reviewed the new sticker pack.", in: app)
        self.wait { (self.editor(app).value as? String) == self.longDraft }
        self.putAside(app)
        self.restore("Yes — 18:00 works.", in: app)
        self.putAside(app)

        self.element(app, "DraftPocket.Open").tap()
        let card = app.descendants(matching: .any).matching(NSPredicate(format:
            "identifier BEGINSWITH %@ AND label CONTAINS %@", "DraftPocket.Restore.", "Remember to review")).firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: 10))
        card.swipeLeft()
        let delete = app.buttons["Delete"].firstMatch
        XCTAssertTrue(delete.waitForExistence(timeout: 5))
        self.event("delete_start")
        delete.tap()
        let undo = self.element(app, "DraftPocketDemo.Undo")
        XCTAssertTrue(undo.waitForExistence(timeout: 5))
        self.event("delete_end")
        self.event("undo_start")
        undo.tap()
        self.wait { card.exists }
        self.event("undo_end")
        self.snapshot("production-delete-undo", app: app)
        self.element(app, "DraftPocket.Close").tap()

        app.terminate()
        app = self.launch(namespace: namespace, secondary: true)
        self.wait { (self.element(app, "DraftPocket.Open").value as? String) == "0" }
        self.type("Only Casey should contain this draft.", in: app)
        self.putAside(app)
        self.wait { (self.element(app, "DraftPocket.Open").value as? String) == "1" }

        app.terminate()
        app = self.launch(namespace: namespace)
        self.wait { (self.element(app, "DraftPocket.Open").value as? String) == "3" }
        self.restore("I’ve reviewed the new sticker pack.", in: app)
        self.wait { (self.editor(app).value as? String) == self.longDraft }
        self.snapshot("production-composer-restored-after-relaunch-offline-fixture", app: app)
        self.event("qa_end")
    }
}
