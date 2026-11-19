"""Manual authorization, exact native proof, and owned lifecycle for the editor lane."""

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("editor_simulator", ROOT / "scripts/editor-simulator.py")
editor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(editor)


class EditorAuthorizationTests(unittest.TestCase):
    repository = "WizardLoop/telegram-draft-pocket"

    def event(self, name="pull_request", **changes):
        event = {"repository": {"private": False, "full_name": self.repository}, "action": "labeled",
                 "label": {"name": "run-editor-simulator"},
                 "pull_request": {"head": {"repo": {"full_name": self.repository}}},
                 "inputs": {"lane": "editor", "included_usage_verified": True}}
        event.update(changes)
        return event

    def workflow_guard(self, job, event, name):
        # Exercise the actual job expression against event scenarios. This keeps
        # the workflow and runtime guards from silently drifting apart.
        workflow = (ROOT / ".github/workflows/simulator.yml").read_text()
        block = re.search(r"(?ms)^  " + job + r":\n    if: >-\n\s*\$\{\{(.*?)\}\}", workflow)[1]
        expression = " ".join(block.split())
        self.assertRegex(expression, r"^[A-Za-z0-9_.'\s()=&|!-]+$")
        expression = expression.replace("&&", " and ").replace("||", " or ").replace("false", "False")
        def namespace(value):
            return SimpleNamespace(**{k: namespace(v) for k, v in value.items()}) if isinstance(value, dict) else value
        context = {"github": namespace({"event": event, "event_name": name, "repository": self.repository}),
                   "inputs": namespace(event["inputs"])}
        return bool(eval(expression, {"__builtins__": {}}, context))

    def test_both_guards_accept_only_the_explicit_public_same_repository_editor_request(self):
        variants = []
        for name in ("pull_request", "workflow_dispatch"):
            variants.append((name, self.event(), True))
            private = self.event(); private["repository"]["private"] = True
            variants.append((name, private, False))
        fork = self.event(); fork["pull_request"]["head"]["repo"]["full_name"] = "someone/fork"
        variants.append(("pull_request", fork, False))
        full = self.event(); full["label"]["name"] = "run-simulator"
        variants.append(("pull_request", full, False))
        other = self.event(); other["inputs"]["lane"] = "full-client"
        variants.append(("workflow_dispatch", other, False))
        unverified = self.event(); unverified["inputs"]["included_usage_verified"] = False
        variants.append(("workflow_dispatch", unverified, False))
        for name, event, allowed in variants:
            with self.subTest(name=name, event=event):
                self.assertEqual(editor.authorized_event(event, name, self.repository), allowed)
                self.assertEqual(self.workflow_guard("editor", event, name), allowed)
        self.assertFalse(self.workflow_guard("simulator", self.event(), "pull_request"))
        self.assertFalse(self.workflow_guard("simulator", self.event(), "workflow_dispatch"))
        self.assertTrue(self.workflow_guard("simulator", full, "pull_request"))
        self.assertTrue(self.workflow_guard("simulator", other, "workflow_dispatch"))

    def test_malformed_or_nonmanual_events_are_refused(self):
        for event in (None, {}, {"repository": None}, self.event(inputs=None), self.event(inputs={"lane": "editor", "included_usage_verified": 1})):
            with self.subTest(event=event):
                self.assertFalse(editor.authorized_event(event, "workflow_dispatch", self.repository))
        self.assertFalse(editor.authorized_event(self.event(), "push", self.repository))
        self.assertFalse(editor.authorized_event(self.event(), "pull_request", None))

    def test_host_credentials_and_artifact_bounds_fail_before_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            event_file = root / "event.json"
            event_file.write_text(json.dumps(self.event()))
            environment = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
                "GITHUB_EVENT_PATH": str(event_file), "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_REPOSITORY": self.repository, "RUNNER_TEMP": str(root)}
            with mock.patch.dict(os.environ, environment, clear=True):
                self.assertEqual(editor.hosted_paths(root / "evidence"), (root, root / "evidence"))
                for change in ({"RUNNER_ENVIRONMENT": "self-hosted"}, {"GITHUB_ACTIONS": "false"},
                               {"DRAFT_POCKET_BUILD_CONFIGURATION_JSON": "must-never-log"}):
                    with mock.patch.dict(os.environ, change), self.assertRaises(editor.package.PackageError) as caught:
                        editor.hosted_paths(root / "evidence")
                    self.assertNotIn("must-never-log", str(caught.exception))
                with self.assertRaises(editor.package.PackageError):
                    editor.hosted_paths(root.parent / "outside")


class EditorNativeEvidenceTests(unittest.TestCase):
    def cases(self):
        return [{"identifier": "RichTextEditorUIKitTests/DocumentTextSelectionTests/" + name + "()", "status": "Success"}
                for name in sorted(editor.EXPECTED_METHODS)]

    def test_all_three_exact_successes_are_required_without_crash_restart_or_native_issues(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "raw.log"; log.write_text("** TEST EXECUTE SUCCEEDED **\n")
            self.assertEqual(editor.verify_three_cases(self.cases(), {}, log), self.cases())
            prefixed = self.cases()
            for case in prefixed:
                case["identifier"] = case["identifier"].replace("RichTextEditorUIKitTests/", "RichTextEditorUIKitTests.")
            editor.verify_three_cases(prefixed, {}, log)
            variants = [self.cases()[:2], self.cases() + self.cases()[:1], self.cases()[:2] + self.cases()[:1]]
            for status in ("Failure", "Skipped", "Expected Failure"):
                cases = self.cases(); cases[0]["status"] = status; variants.append(cases)
            wrong = self.cases(); wrong[0]["identifier"] += "Fake"; variants.append(wrong)
            for cases in variants:
                with self.subTest(cases=cases), self.assertRaises(editor.package.PackageError):
                    editor.verify_three_cases(cases, {}, log)
            for diagnostic in ("Fatal error: unowned reference", "Restarting after unexpected exit, crash, or test timeout"):
                log.write_text(diagnostic + "\nExecuted 3 tests, with 0 failures\n")
                with self.assertRaisesRegex(editor.package.PackageError, "crash"):
                    editor.verify_three_cases(self.cases(), {}, log)
            log.write_text("three passed\n")
            with self.assertRaisesRegex(editor.package.PackageError, "xcresult contains"):
                editor.verify_three_cases(self.cases(), {"issues": {"testFailureSummaries": {"_values": [{"message": "crash"}]}}}, log)

    def test_real_native_schema_is_exported_without_opaque_bundle_or_other_payloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); bundle = root / "native.xcresult"; bundle.mkdir()
            (bundle / "opaque-diagnostic").write_bytes(b"never upload")
            artifacts = root / "evidence"; artifacts.mkdir()
            log = artifacts / "test.log"; log.write_text("Test Suite passed\n")
            native_root = {"actions": {"_values": [{"_type": {"_name": "ActionRecord"},
                "actionResult": {"testsRef": {"id": {"_value": "0~native-tests"}}}}]}}
            tests = {"summaries": {"_values": [{"testableSummaries": {"_values": [{"tests": {"_values": [{
                "identifier": {"_value": "DocumentTextSelectionTests"}, "subtests": {"_values": [
                    {"identifier": {"_value": name + "()"}, "testStatus": {"_value": "Success"}}
                    for name in sorted(editor.EXPECTED_METHODS)]}}]}}]}}]}}
            values = [native_root, tests]
            def execute(command, **kwargs):
                self.assertEqual(command[:5], ["xcrun", "xcresulttool", "get", "--legacy", "--format"])
                self.assertEqual(kwargs["timeout"], 30)
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps(values.pop(0)).encode(), stderr=b"")
            with mock.patch.object(editor.subprocess, "run", side_effect=execute):
                proof = editor.native_evidence(bundle, artifacts, log)
            self.assertEqual(proof["status"], "passed")
            self.assertEqual(len(proof["cases"]), 3)
            self.assertTrue((artifacts / "xcresult-root.json").is_file())
            self.assertTrue((artifacts / "xcresult-tests-01.json").is_file())
            self.assertFalse(any(path.suffix == ".xcresult" or path.name == "opaque-diagnostic" for path in artifacts.rglob("*")))


class EditorLifecycleTests(unittest.TestCase):
    def test_build_then_boot_then_native_evidence_with_owned_cleanup_and_no_bazel(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); artifacts = root / "evidence"; artifacts.mkdir()
            calls = []
            session = mock.Mock(udid="CA1C775E-9033-4666-9B09-1E195CA035B5", runtime="iOS27")
            for method in ("create", "boot", "cleanup"):
                getattr(session, method).side_effect = lambda *args, method=method: calls.append(method)
            def run(name, command, **kwargs):
                calls.append(name)
                self.assertIn("platform=iOS Simulator,id=" + session.udid, command)
                self.assertIn("-only-testing:" + editor.FILTER, command)
                self.assertNotIn("bazel", " ".join(str(x) for x in command))
                if name == "test-without-building":
                    (root / "selection-tests.xcresult").mkdir()
            commands = mock.Mock(run=run)
            with mock.patch.object(editor, "SimulatorSession", return_value=session) as factory, \
                    mock.patch.object(editor, "native_evidence", side_effect=lambda *args: calls.append("native-proof") or {"status": "passed"}):
                editor.execute_tests(root / "checkout", root, artifacts, {"device_type": "iPhone 17", "os_version": "27.0"}, commands)
            self.assertEqual(calls, ["create", "build-for-testing", "boot", "test-without-building", "native-proof", "cleanup"])
            self.assertEqual(factory.call_args.kwargs["boot_timeout"], 300)
            session.release_build_server.assert_not_called()

    def test_build_test_proof_and_cleanup_failures_cannot_become_success(self):
        for phase in ("build-for-testing", "boot", "test-without-building", "native-proof"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); session = mock.Mock(udid="CA1C775E-9033-4666-9B09-1E195CA035B5", runtime="iOS27")
                session.cleanup.side_effect = editor.package.PackageError("cleanup failed")
                if phase == "boot": session.boot.side_effect = editor.package.PackageError("boot failed")
                commands = mock.Mock()
                def run(name, command, **kwargs):
                    if name == "test-without-building": (root / "selection-tests.xcresult").mkdir()
                    if name == phase: raise editor.package.PackageError(phase + " failed")
                commands.run.side_effect = run
                proof = mock.Mock(return_value={"status": "passed"})
                if phase == "native-proof": proof.side_effect = editor.package.PackageError("native-proof failed")
                with mock.patch.object(editor, "SimulatorSession", return_value=session), \
                        mock.patch.object(editor, "native_evidence", proof), self.assertRaises(editor.package.PackageError) as caught:
                    editor.execute_tests(root / "checkout", root, root, {"device_type": "iPhone 17", "os_version": "27.0"}, commands)
                self.assertIn(phase + " failed", str(caught.exception))
                self.assertIn("cleanup failed", str(caught.exception))
                session.cleanup.assert_called_once()
                if phase == "build-for-testing": session.boot.assert_not_called()

    def test_timeout_keeps_the_unfiltered_log_and_stops_only_owned_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = editor.Commands(root)
            with self.assertRaisesRegex(editor.package.PackageError, "exceeded"):
                commands.run("owned-child", [sys.executable, "-c", "import time;print('raw diagnostic',flush=True);time.sleep(20)"], timeout=2)
            self.assertIn("raw diagnostic", (root / "owned-child.log").read_text())
            self.assertEqual(commands.events[0]["status"], "timed_out")

    def test_shared_boot_timeout_default_and_editor_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(editor.SimulatorSession(root / "default").boot_timeout, 300)
            self.assertEqual(editor.SimulatorSession(root / "editor", boot_timeout=300).boot_timeout, 300)
            for value in (0, 29, 301, True, 180.0, "300"):
                with self.subTest(value=value), self.assertRaises(editor.package.PackageError):
                    editor.SimulatorSession(root / "invalid", boot_timeout=value)


if __name__ == "__main__":
    unittest.main()
