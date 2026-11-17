"""Reject plausible-looking recordings that lack real scenario evidence."""

import importlib.util
from contextlib import ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
SPEC = importlib.util.spec_from_file_location("capture_demo", Path(__file__).parents[1] / "scripts/capture-demo.py")
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)
from simulator import _SimulatorCommandFailure


class CaptureSelectionTests(unittest.TestCase):
    def invoke(self, extra):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            argv = ["--checkout", str(root / "checkout"), "--configuration", str(root / "config"),
                    "--cache", str(root / "cache"), "--artifacts", str(root / "evidence"), *extra]
            with mock.patch.object(capture, "capture") as run:
                self.assertEqual(capture.main(argv), 0)
            return root, run.call_args_list

    def test_default_all_keeps_only_the_two_existing_takes(self):
        root, calls = self.invoke([])
        self.assertEqual([(call.args[3].name, call.args[4], call.kwargs) for call in calls], [
            ("story", "DraftPocketDemoUITests/testCaptureProductionStory", {"appearance": None}),
            ("qa", "DraftPocketDemoUITests/testQASwapDeleteUndoRestartAndChatIsolation", {"appearance": None})])
        expected_paths = tuple((root / name).resolve() for name in ("checkout", "config", "cache"))
        self.assertTrue(all(call.args[:3] == expected_paths for call in calls))

    def test_explicit_opt_in_appends_third_separate_take_using_same_real_qa_filter(self):
        _, calls = self.invoke(["--include-dark-qa"])
        self.assertEqual([call.args[3].name for call in calls], ["story", "qa", "qa-dark"])
        self.assertEqual(calls[1].args[4], calls[2].args[4])
        self.assertEqual([call.kwargs["appearance"] for call in calls], [None, None, "dark"])
        self.assertEqual(len({call.args[3] for call in calls}), 3)

    def test_standalone_dark_selection_does_not_run_or_replace_baseline_takes(self):
        _, calls = self.invoke(["--scenario", "qa-dark"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[3].name, "qa-dark")
        self.assertEqual(calls[0].args[4], "DraftPocketDemoUITests/testQASwapDeleteUndoRestartAndChatIsolation")
        self.assertEqual(calls[0].kwargs, {"appearance": "dark"})

    def test_dark_failure_propagates_to_cli_exit_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            argv = ["--checkout", temporary, "--configuration", temporary, "--cache", temporary,
                    "--artifacts", temporary, "--scenario", "qa-dark"]
            with mock.patch.object(capture, "capture", side_effect=capture.package.PackageError("readback mismatch")), \
                    mock.patch("sys.stderr"):
                self.assertEqual(capture.main(argv), 1)

    def test_conflicting_opt_in_fails_before_capture_or_artifact_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "not-created"
            argv = ["--checkout", temporary, "--configuration", temporary, "--cache", temporary,
                    "--artifacts", str(root), "--scenario", "qa", "--include-dark-qa"]
            with mock.patch.object(capture, "capture") as run, mock.patch("sys.stderr"), \
                    self.assertRaises(SystemExit) as error:
                capture.main(argv)
            self.assertEqual(error.exception.code, 2)
            run.assert_not_called()
            self.assertFalse(root.exists())


class DarkCaptureFlowTests(unittest.TestCase):
    def run_take(self, artifacts, *, before=b"dark\n", after=b"dark\n", test_code=0,
                 recorder_exit=0, exit_during_provenance=None, recorder_timeout=False,
                 native_error=None, launch_error=None, crash_error=None,
                 emitted_timeout=False, supervision_errors=(), build_code=0, validation_error=None,
                 status_bar_unavailable=False, framebuffer_error=None, cleanup_error=None):
        self.selection = "DraftPocketDemoUITests/testQASwapDeleteUndoRestartAndChatIsolation"
        self.trace = []
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.checkout = Path(temporary.name).resolve()
        self.session = capture.SimulatorSession(artifacts)
        self.session.udid = "CA1C775E-9033-4666-9B09-1E195CA035B5"
        self.session.runtime = "ios-runtime"
        self.session._owned = True
        for name in ("release_build_server", "create", "prepare_framebuffer", "boot",
                     "probe_framebuffer", "cleanup", "health"):
            setattr(self.session, name, mock.Mock(side_effect=lambda *args, name=name:
                self.trace.append(name) or (self.session.udid if name == "create" else None)))
        def cleanup():
            self.trace.append("cleanup")
            if cleanup_error:
                raise cleanup_error
            self.session._owned = False
            self.session._delete_confirmed = True
        self.session.cleanup.side_effect = cleanup
        if framebuffer_error:
            self.session.probe_framebuffer.side_effect = framebuffer_error
        def phase(name, command, **kwargs):
            self.trace.append(name)
            if name == "status-bar" and status_bar_unavailable:
                raise _SimulatorCommandFailure("status bar timed out", "timed_out")
            return before if name == "appearance-before-test" else after if name == "appearance-after-test" else b""
        self.session._run = mock.Mock(side_effect=phase)
        manifest = {"feature": {"patch": {"sha256": "feature"}}, "demo": {"patch": {"sha256": "demo"},
            "native_test_target": "//Tests/DraftPocketDemo:DraftPocketDemoUITests", "device_type": "iPhone 17", "os_version": "27.0"}}
        config = {"api_id": "12345", "api_hash": "owner-secret", "team_id": "LOCALTEAM", "app_center_id": "0", "bundle_id": "org.example.fixture"}
        events = ["qa_start", "put_aside_start", "put_aside_end", "restore_start", "restore_end",
                  "delete_start", "delete_end", "undo_start", "undo_end", "qa_end"]
        recorder = mock.Mock()
        recorder.poll.return_value = None
        recorder_waits = []
        def wait_for_stop(*, timeout):
            recorder_waits.append(timeout)
            if recorder_timeout and len(recorder_waits) == 1:
                raise subprocess.TimeoutExpired("recorder", timeout)
            recorder.poll.return_value = recorder_exit
            return recorder_exit
        recorder.wait.side_effect = wait_for_stop
        self.recorder = recorder
        def verify_provenance(*args):
            if exit_during_provenance is not None:
                recorder.poll.return_value = exit_during_provenance
            return {"matched": True}
        def launch_report(*args):
            self.trace.append("launch-report")
            if launch_error:
                raise launch_error
            return {"status": "saved"}
        def crash_summary(*args):
            self.trace.append("crash-summary")
            if crash_error:
                raise crash_error
            return {"status": "absent", "reason": "no_matching_crash_report"}
        def spawn(command, **kwargs):
            if "--scriptPath" in command:
                self.trace.append("build")
                self.assertEqual(command[-8:-2], ["--target", manifest["demo"]["native_test_target"],
                    "--simulatorUDID", self.session.udid, "--testFilter", self.selection])
                self.assertFalse(Path(command[-1]).resolve().is_relative_to(artifacts.resolve()))
                return mock.Mock(stdout=[], wait=lambda: build_code)
            if "recordVideo" in command:
                self.trace.append("recorder")
                (artifacts / "draft-pocket-continuous-raw.mov").write_bytes(b"test-video-placeholder")
                return recorder
            self.fail("Capture must not spawn Make/Bazel after boot")
        self.plan = SimpleNamespace(output_directory=self.checkout / "verified-outputs" / "test.outputs",
                                    test_directory=self.checkout / "verified-outputs", identity={"script_sha256": "script-hash"})
        def validate(*args, **kwargs):
            self.trace.append("validate")
            self.assertEqual(args[0], self.checkout)
            self.assertEqual(kwargs, {"target": manifest["demo"]["native_test_target"],
                                     "test_filter": self.selection, "udid": self.session.udid})
            if validation_error:
                raise validation_error
            return self.plan
        def execute(plan, destination, *, stage, timeout_seconds, on_line):
            self.trace.append("xctest")
            self.assertIs(plan, self.plan)
            self.assertEqual(destination, artifacts)
            self.assertEqual(stage, "demo-xctest")
            self.assertEqual(timeout_seconds, 600)
            on_line("runner owner-secret\n")
            for name in events:
                on_line("DRAFT_POCKET_EVENT " + json.dumps({"event": name, "utc": time.time()}) + "\n")
            return SimpleNamespace(returncode=test_code, timed_out=emitted_timeout,
                                   supervision_errors=list(supervision_errors), output_directory=plan.output_directory)
        with ExitStack() as stack:
            def patch(target, name, **kwargs):
                return stack.enter_context(mock.patch.object(target, name, **kwargs))
            patch(capture.package, "toolchain")
            patch(capture.package, "validate_package", return_value=({}, manifest))
            patch(capture.package.shutil, "which", return_value="tool")
            patch(capture.package, "load_json", return_value=config)
            self.make = patch(capture.package, "make_command", return_value=["make", "test"])
            patch(capture.package, "run", return_value=b"/owned/Telegram.app\n")
            patch(capture, "built_binary_inventory", return_value={"files": []})
            self.provenance = patch(capture, "verify_installed_binaries", side_effect=verify_provenance)
            self.native = patch(capture, "native_results", side_effect=native_error,
                                return_value=[{"identifier": self.selection + "()", "status": "Success"}])
            self.launch_report = patch(capture, "export_launch_report", side_effect=launch_report)
            self.crash_summary = patch(capture, "export_crash_summaries", side_effect=crash_summary)
            self.media = patch(capture, "inspect_media")
            patch(capture, "SimulatorSession", return_value=self.session)
            self.validate = patch(capture, "validate_emitted_script", side_effect=validate)
            self.execute = patch(capture, "run_emitted", side_effect=execute)
            patch(capture.subprocess, "Popen", side_effect=spawn)
            patch(capture, "wait_for_recorder")
            patch(capture, "print")
            capture.capture(self.checkout, artifacts / "config", artifacts / "cache", artifacts, self.selection, appearance="dark")

    def test_verified_dark_runs_same_case_on_owned_uuid_and_keeps_all_evidence_gates(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = Path(temporary)
            self.run_take(artifacts)
            identity = json.loads((artifacts / "capture-identity.json").read_text())
            self.assertEqual(identity["appearance"]["requested"], "dark")
            self.assertEqual([check["phase"] for check in identity["appearance"]["checks"]], ["before-test", "after-test"])
            self.assertTrue(all(check["matched"] and check["udid"] == identity["udid"] for check in identity["appearance"]["checks"]))
            self.assertEqual(identity["reported_test_cases"], 1)
            self.assertTrue(identity["ownership_released"])
            self.assertEqual(identity["emitted_runner"], self.plan.identity)
            self.assertEqual(identity["status_bar"]["status"], "applied")
            self.crash_summary.assert_not_called()
            for before, after in zip(("create", "build", "validate", "release_build_server"),
                                     ("build", "validate", "release_build_server", "boot")):
                self.assertLess(self.trace.index(before), self.trace.index(after))
            self.assertLess(self.trace.index("boot"), self.trace.index("appearance-set"))
            self.assertLess(self.trace.index("appearance-before-test"), self.trace.index("recorder"))
            self.assertLess(self.trace.index("recorder"), self.trace.index("xctest"))
            self.assertLess(self.trace.index("xctest"), self.trace.index("appearance-after-test"))
            self.provenance.assert_called_once()
            self.native.assert_called_once_with(self.plan.test_directory, artifacts, mock.ANY, self.selection,
                                                output_directory=self.plan.output_directory)
            self.make.assert_called_once()
            self.media.assert_called_once()
            self.session.cleanup.assert_called_once()
            self.session.create.assert_called_once()
            self.assertNotIn("owner-secret", (artifacts / "demo-xctest.log").read_text())
            self.assertIn("[REDACTED]", (artifacts / "demo-xctest.log").read_text())

    def test_emission_or_validation_failure_cleans_created_device_before_any_boot(self):
        for build_code, validation_error in ((7, None), (0, capture.package.PackageError("invalid emitted identity"))):
            with self.subTest(build_code=build_code), tempfile.TemporaryDirectory() as temporary:
                artifacts = Path(temporary)
                with self.assertRaisesRegex(capture.package.PackageError, "failed to build/emit|invalid emitted identity"):
                    self.run_take(artifacts, build_code=build_code, validation_error=validation_error)
                self.session.create.assert_called_once()
                self.session.boot.assert_not_called()
                self.session.cleanup.assert_called_once()
                self.execute.assert_not_called()
                self.assertNotIn("recorder", self.trace)
                self.assertTrue(json.loads((artifacts / "capture-identity.json").read_text())["ownership_released"])

    def test_native_deadline_or_group_cleanup_failure_cannot_pass_on_zero_exit(self):
        for timed_out, errors, message in ((True, (), "600s execution deadline"),
                                           (False, ("owned group did not drain",), "process cleanup failed")):
            with self.subTest(timed_out=timed_out), tempfile.TemporaryDirectory() as temporary:
                artifacts = Path(temporary)
                with self.assertRaisesRegex(capture.package.PackageError, message):
                    self.run_take(artifacts, emitted_timeout=timed_out, supervision_errors=errors)
                identity = json.loads((artifacts / "capture-identity.json").read_text())
                self.assertEqual(identity["xctest_exit_code"], 0)
                self.assertEqual(identity["xctest_timed_out"], timed_out)
                self.assertEqual(identity["xctest_supervision_errors"], list(errors))
                self.assertNotIn("reported_test_cases", identity)
                self.native.assert_called_once_with(self.plan.test_directory, artifacts, mock.ANY, self.selection,
                                                    diagnostic=True, output_directory=self.plan.output_directory)
                self.provenance.assert_not_called()
                self.recorder.send_signal.assert_called_once()
                self.session.cleanup.assert_called_once()

    def test_unavailable_cosmetic_status_bar_continues_only_with_real_framebuffer_gate(self):
        for framebuffer_error in (None, capture.package.PackageError("framebuffer unavailable")):
            with self.subTest(framebuffer_error=framebuffer_error), tempfile.TemporaryDirectory() as temporary:
                artifacts = Path(temporary)
                if framebuffer_error:
                    with self.assertRaisesRegex(capture.package.PackageError, "framebuffer unavailable"):
                        self.run_take(artifacts, status_bar_unavailable=True, framebuffer_error=framebuffer_error)
                    self.execute.assert_not_called()
                    self.assertNotIn("recorder", self.trace)
                else:
                    self.run_take(artifacts, status_bar_unavailable=True)
                    self.execute.assert_called_once()
                identity = json.loads((artifacts / "capture-identity.json").read_text())
                self.assertEqual(identity["status_bar"]["status"], "unavailable")
                self.assertEqual(identity["status_bar"]["reason"], "timed_out")
                self.session.probe_framebuffer.assert_called_once()
                self.session.cleanup.assert_called_once()

    def test_failed_owned_cleanup_exposes_unreleased_device_for_next_stage_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = Path(temporary)
            with self.assertRaisesRegex(capture.package.PackageError, "delete unconfirmed"):
                self.run_take(artifacts, cleanup_error=capture.package.PackageError("delete unconfirmed"))
            identity = json.loads((artifacts / "capture-identity.json").read_text())
            self.assertFalse(identity["ownership_released"])
            self.assertIn("delete unconfirmed", identity["cleanup_error"])

    def test_failed_initial_readback_prevents_recorder_and_xctest_and_cleans_device(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = Path(temporary)
            with self.assertRaisesRegex(capture.package.PackageError, "does not exactly match dark"):
                self.run_take(artifacts, before=b"light\n")
            self.assertNotIn("recorder", self.trace)
            self.assertNotIn("xctest", self.trace)
            self.assertNotIn("reported_test_cases", json.loads((artifacts / "capture-identity.json").read_text()))
            self.session.cleanup.assert_called_once()

    def test_reset_appearance_after_successful_case_cannot_pass_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = Path(temporary)
            with self.assertRaisesRegex(capture.package.PackageError, "does not exactly match dark"):
                self.run_take(artifacts, after=b"light\n")
            self.assertIn("xctest", self.trace)
            self.provenance.assert_not_called()
            self.assertNotIn("reported_test_cases", json.loads((artifacts / "capture-identity.json").read_text()))
            self.media.assert_called_once()
            self.session.cleanup.assert_called_once()

    def test_dark_setting_cannot_mask_failed_native_case(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = Path(temporary)
            with self.assertRaisesRegex(capture.package.PackageError, "XCTest failed with exit 7"):
                self.run_take(artifacts, test_code=7)
            identity = json.loads((artifacts / "capture-identity.json").read_text())
            self.assertEqual(identity["xctest_exit_code"], 7)
            self.assertNotIn("reported_test_cases", identity)
            self.native.assert_called_once_with(self.plan.test_directory, artifacts, mock.ANY, self.selection,
                                                diagnostic=True, output_directory=self.plan.output_directory)
            self.launch_report.assert_called_once()
            self.assertEqual(identity["launch_diagnostics"]["status"], "saved")
            self.assertEqual(identity["crash_diagnostics"]["status"], "absent")
            self.crash_summary.assert_called_once()
            self.assertLess(self.trace.index("crash-summary"), self.trace.index("cleanup"))
            self.assertLess(self.trace.index("launch-report"), self.trace.index("cleanup"))
            self.session.cleanup.assert_called_once()

    def test_diagnostic_export_failure_preserves_xctest_failure_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = Path(temporary)
            with self.assertRaisesRegex(capture.package.PackageError, "XCTest failed with exit 7"):
                self.run_take(artifacts, test_code=7,
                              native_error=capture.package.PackageError("unreadable native result"),
                              launch_error=capture.package.PackageError("unvalidated private response"),
                              crash_error=OSError("unvalidated private report"))
            content = (artifacts / "capture-identity.json").read_text()
            identity = json.loads(content)
            self.assertEqual(identity["xctest_exit_code"], 7)
            self.assertEqual(identity["launch_diagnostics"]["status"], "unavailable")
            self.assertEqual(identity["crash_diagnostics"]["status"], "unavailable")
            self.assertIn("unreadable native result", identity["native_failure_evidence_error"])
            self.assertNotIn("unvalidated private response", content)
            self.assertNotIn("unvalidated private report", content)
            self.assertNotIn("reported_test_cases", identity)
            self.assertLess(self.trace.index("launch-report"), self.trace.index("cleanup"))
            self.session.cleanup.assert_called_once()
            self.media.assert_called_once()

    def test_recorder_exit_after_xctest_before_intentional_stop_cannot_pass(self):
        for code in (0, 7):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                artifacts = Path(temporary)
                with self.assertRaisesRegex(capture.package.PackageError, f"Recorder exited {code} before intentional stop"):
                    self.run_take(artifacts, exit_during_provenance=code)
                identity = json.loads((artifacts / "capture-identity.json").read_text())
                self.assertFalse(identity["recorder_alive_before_stop"])
                self.assertEqual(identity["recorder_exit_code"], code)
                self.assertNotIn("recorder_stop_requested_utc", identity)
                self.assertEqual(identity["reported_test_cases"], 1)  # A passed UI case cannot override a failed take.
                self.assertEqual((artifacts / "draft-pocket-continuous-raw.mov").read_bytes(), b"test-video-placeholder")
                self.media.assert_called_once()
                self.session.cleanup.assert_called_once()

    def test_nonzero_recorder_finalization_is_not_accepted_as_a_normal_sigint_stop(self):
        for code in (7, -capture.signal.SIGINT):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                artifacts = Path(temporary)
                with self.assertRaisesRegex(capture.package.PackageError, f"Recorder finalization exited {code}"):
                    self.run_take(artifacts, recorder_exit=code)
                identity = json.loads((artifacts / "capture-identity.json").read_text())
                self.assertTrue(identity["recorder_alive_before_stop"])
                self.assertIn("recorder_stop_requested_utc", identity)
                self.assertEqual(identity["recorder_exit_code"], code)
                self.assertTrue(identity["secondary_errors"])
                self.assertEqual((artifacts / "draft-pocket-continuous-raw.mov").read_bytes(), b"test-video-placeholder")
                self.media.assert_called_once()
                self.session.cleanup.assert_called_once()

    def test_recorder_finalization_timeout_remains_failed_with_bounded_forced_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = Path(temporary)
            with self.assertRaisesRegex(capture.package.PackageError, "Recorder did not finalize after SIGINT"):
                self.run_take(artifacts, recorder_exit=-9, recorder_timeout=True)
            self.assertEqual(self.recorder.wait.call_args_list, [mock.call(timeout=30), mock.call(timeout=5)])
            self.recorder.kill.assert_called_once()
            identity = json.loads((artifacts / "capture-identity.json").read_text())
            self.assertTrue(identity["recorder_forced_termination"])
            self.assertEqual(identity["recorder_exit_code"], -9)
            self.assertEqual((artifacts / "draft-pocket-continuous-raw.mov").read_bytes(), b"test-video-placeholder")
            self.media.assert_called_once()
            self.session.cleanup.assert_called_once()


class RecorderReadinessTests(unittest.TestCase):
    def test_startup_failure_cleans_owned_device_and_never_starts_recorder_or_xctest(self):
        for failed_method, disk_failure in (("release_build_server", False), ("boot", False), ("probe_framebuffer", False), ("boot", True)):
            with self.subTest(phase=failed_method, disk_failure=disk_failure), tempfile.TemporaryDirectory() as temporary:
                artifacts = Path(temporary) / "evidence"
                artifacts.mkdir()
                checkout = Path(temporary) / "checkout"
                checkout.mkdir()
                session = mock.Mock(udid="CA1C775E-9033-4666-9B09-1E195CA035B5", runtime="ios-runtime", ownership_released=False)
                session.create.return_value = session.udid
                session.attempt_capture_status_bar.return_value = {"status": "applied"}
                disk = {"failed": False}
                def fail_startup(*args):
                    disk["failed"] = disk_failure
                    raise capture.package.PackageError("startup failed")
                getattr(session, failed_method).side_effect = fail_startup
                original_write = Path.write_text
                def write(path, *args, **kwargs):
                    if disk["failed"]:
                        raise OSError("capture disk full")
                    return original_write(path, *args, **kwargs)
                session.cleanup.side_effect = capture.package.PackageError("cleanup also failed")
                manifest = {"feature": {"patch": {"sha256": "feature"}}, "demo": {"patch": {"sha256": "demo"},
                    "native_test_target": "//Tests/DraftPocketDemo:DraftPocketDemoUITests", "device_type": "iPhone 17", "os_version": "27.0"}}
                config = {"api_id": "12345", "api_hash": "owner-secret", "team_id": "LOCALTEAM", "app_center_id": "0", "bundle_id": "org.example.fixture"}
                build = mock.Mock(stdout=[], wait=lambda: 0)
                with mock.patch.object(capture.package, "toolchain"), \
                        mock.patch.object(capture.package, "validate_package", return_value=({}, manifest)), \
                        mock.patch.object(capture.package.shutil, "which", return_value="tool"), \
                        mock.patch.object(capture.package, "load_json", return_value=config), \
                        mock.patch.object(capture.package, "make_command", return_value=["build"]), \
                        mock.patch.object(capture, "built_binary_inventory", return_value={"files": []}), \
                        mock.patch.object(capture, "validate_emitted_script", return_value=SimpleNamespace(identity={})), \
                        mock.patch.object(capture, "run_emitted") as execute, \
                        mock.patch.object(capture, "SimulatorSession", return_value=session), \
                        mock.patch.object(capture.subprocess, "Popen", return_value=build) as spawn, \
                        mock.patch.object(Path, "write_text", new=write), \
                        self.assertRaisesRegex(capture.package.PackageError, "startup failed") as failure:
                    capture.capture(checkout, artifacts / "config", artifacts / "cache", artifacts,
                                    "DraftPocketDemoUITests/testCaptureProductionStory")
                self.assertEqual(spawn.call_count, 1)  # Only the preparation build ran.
                session.cleanup.assert_called_once()
                self.assertEqual(session.method_calls[0], mock.call.create())
                execute.assert_not_called()
                if failed_method == "release_build_server":
                    session.create.assert_called_once()
                    session.boot.assert_not_called()
                session.health.assert_called_once_with("capture-failure-before-recorder-stop")
                self.assertIn("cleanup also failed", str(failure.exception))
                if disk_failure:
                    self.assertIn("capture disk full", str(failure.exception))
                    continue
                identity = json.loads((artifacts / "capture-identity.json").read_text())
                self.assertEqual(identity["cleanup_error"], "cleanup also failed")
                self.assertNotIn("recording_ready_utc", identity)

    def test_native_ready_without_newline_is_observed_and_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "recorder.log"
            with path.open("wb") as log:
                process = subprocess.Popen([sys.executable, "-c",
                    "import sys,time;sys.stdout.write('Recording started');sys.stdout.flush();time.sleep(10)"],
                    stdout=log, stderr=subprocess.STDOUT)
                try:
                    capture.wait_for_recorder(process, path, timeout=5)
                    self.assertIsNone(process.poll())
                    self.assertEqual(path.read_bytes(), b"Recording started")
                finally:
                    process.kill()
                    process.wait(timeout=5)

    def test_exited_recorder_cannot_pass_even_with_ready_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "recorder.log"
            path.write_bytes(b"Recording started\nencoder failed\n")
            with self.assertRaisesRegex(capture.package.PackageError, "exited 7"):
                capture.wait_for_recorder(mock.Mock(poll=lambda: 7), path)
            self.assertIn(b"encoder failed", path.read_bytes())

    def test_silent_live_recorder_does_not_start_xctest(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "recorder.log"
            path.write_bytes(b"")
            with self.assertRaisesRegex(capture.package.PackageError, "XCTest was not started"):
                capture.wait_for_recorder(mock.Mock(poll=lambda: None), path, timeout=0)


class CaptureEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.selection = "DraftPocketDemoUITests/testCaptureProductionStory"
        self.cases = [{"identifier": "DraftPocketDemoUITests/testCaptureProductionStory()", "status": "Success"}]
        names = ["story_start", "put_aside_start", "put_aside_end", "incoming_question_visible", "put_aside_start", "put_aside_end",
                 "send_local_start", "send_local_end", "restore_start", "restore_end", "send_local_start", "send_local_end", "story_end"]
        self.events = [{"event": name, "utc": 101 + index} for index, name in enumerate(names)]

    def verify(self, cases=None, events=None):
        return capture.verify_scenario(self.cases if cases is None else cases,
                                       self.events if events is None else events, self.selection, 100, 120)

    def test_requested_scenario_and_ordered_recorded_actions(self):
        self.assertEqual(self.verify(), 1)

    def test_generic_success_or_wrong_test_does_not_prove_capture(self):
        for cases in ('<testsuite><testcase name="GenericSuccess"/></testsuite>', [],
                      [{"identifier": "testUnrelatedAnimation", "status": "Success"}],
                      [{"identifier": "DraftPocketDemoUITests/testCaptureProductionStoryFake", "status": "Success"}],
                      self.cases + [{"identifier": "Other/testOther", "status": "Success"}]):
            with self.subTest(cases=cases), self.assertRaises(capture.package.PackageError):
                self.verify(cases=cases)

    def test_missing_or_reordered_actions_rejected(self):
        for events in ([], self.events[:-1], list(reversed(self.events)), [e for e in self.events if e["event"] != "restore_start"]):
            with self.subTest(events=events), self.assertRaises(capture.package.PackageError):
                self.verify(events=events)

    def test_invalid_or_outside_take_timestamps_rejected(self):
        for instant in (float("nan"), float("inf"), True, "103", 95, 125):
            events = [dict(e) for e in self.events]
            events[3]["utc"] = instant
            with self.subTest(instant=instant), self.assertRaises(capture.package.PackageError):
                self.verify(events=events)

    def test_failed_or_skipped_expected_case_rejected(self):
        for status in ("Failure", "Error", "Skipped", "Expected Failure", "Unknown"):
            cases = [dict(self.cases[0], status=status)]
            with self.subTest(status=status), self.assertRaises(capture.package.PackageError):
                self.verify(cases=cases)


class NativeResultTests(unittest.TestCase):
    def setUp(self):
        self.root = {"actions": {"_values": [{"_type": {"_name": "ActionRecord"},
                     "actionResult": {"testsRef": {"id": {"_value": "0~native-reference"}}}}]}}
        leaf = {"identifier": {"_value": "testCaptureProductionStory()"}, "testStatus": {"_value": "Success"}}
        group = {"identifier": {"_value": "DraftPocketDemoUITests"}, "subtests": {"_values": [leaf]}}
        self.summary = {"summaries": {"_values": [{"testableSummaries": {"_values": [{"tests": {"_values": [group]}}]}}]}}

    def test_native_schema_preserves_parent_class_and_leaf_status(self):
        seen = []
        def read(identifier):
            seen.append(identifier)
            return self.summary
        self.assertEqual(capture.xcresult_cases(self.root, read),
                         [{"identifier": "DraftPocketDemoUITests/testCaptureProductionStory()", "status": "Success"}])
        self.assertEqual(seen, ["0~native-reference"])

    def test_typed_empty_sibling_does_not_hide_the_real_case(self):
        groups = self.summary["summaries"]["_values"][0]["testableSummaries"]["_values"][0]["tests"]["_values"]
        groups.insert(0, {"identifier": {"_value": "EmptySuite"}, "subtests": {"_type": {"_name": "Array"}}})
        cases = capture.xcresult_cases(self.root, lambda _: self.summary)
        self.assertEqual(capture.selected_native_case(cases, "DraftPocketDemoUITests/testCaptureProductionStory")["status"], "Success")
        groups.pop()
        with self.assertRaises(capture.package.PackageError):
            capture.selected_native_case(capture.xcresult_cases(self.root, lambda _: self.summary),
                                         "DraftPocketDemoUITests/testCaptureProductionStory")

    def test_array_reader_distinguishes_typed_empty_from_malformed_or_missing(self):
        self.assertEqual(capture.native_array({"items": {"_type": {"_name": "Array"}}}, "items"), [])
        self.assertEqual(capture.native_array({}, "items", optional=True), [])
        for container in ({}, {"items": {}}, {"items": None}, {"items": {"_type": {"_name": "String"}}},
                          {"items": {"_type": {"_name": "Array"}, "_values": None}}, {"items": {"_values": "bad"}}):
            with self.subTest(container=container), self.assertRaises(capture.package.PackageError):
                capture.native_array(container, "items")

    def test_missing_wrong_or_ambiguous_native_schema_rejected(self):
        for root in ({}, {"actions": None}, {"actions": {"_values": [None]}},
                     {"actions": {"_values": self.root["actions"]["_values"] * 2}}):
            with self.subTest(root=root), self.assertRaises(capture.package.PackageError):
                capture.xcresult_cases(root, lambda _: self.summary)
        with self.assertRaises(capture.package.PackageError):
            capture.xcresult_cases(self.root, lambda _: {"summaries": []})

    def test_native_summary_reference_is_validated_and_retained(self):
        leaf = self.summary["summaries"]["_values"][0]["testableSummaries"]["_values"][0]["tests"]["_values"][0]["subtests"]["_values"][0]
        leaf["summaryRef"] = {"id": {"_value": "0~screenshot-summary"}}
        cases = capture.xcresult_cases(self.root, lambda _: self.summary)
        self.assertEqual(cases[0]["summary_ref"], "0~screenshot-summary")
        leaf["summaryRef"]["id"]["_value"] = "--wrong-reference"
        with self.assertRaises(capture.package.PackageError):
            capture.xcresult_cases(self.root, lambda _: self.summary)

    def test_native_archive_copy_is_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outputs, destination = root / "outputs", root / "copy"
            outputs.mkdir()
            destination.mkdir()
            with zipfile.ZipFile(outputs / "outputs.zip", "w") as zipped:
                zipped.writestr("test.xcresult/Info.plist", b"native fixture bytes")
                zipped.writestr("unrelated.log", b"not copied")
            result = capture.copy_xcresult(outputs, destination)
            self.assertEqual((result / "Info.plist").read_bytes(), b"native fixture bytes")
            self.assertFalse((destination / "unrelated.log").exists())

    def test_archive_traversal_or_link_fails_before_extracting(self):
        for bad in ("test.xcresult/../escape", "/absolute", "test.xcresult/link"):
            with self.subTest(path=bad), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                outputs, destination = root / "outputs", root / "copy"
                outputs.mkdir()
                destination.mkdir()
                with zipfile.ZipFile(outputs / "outputs.zip", "w") as zipped:
                    zipped.writestr("test.xcresult/Info.plist", b"valid but must not be copied")
                    entry = zipfile.ZipInfo(bad)
                    if bad.endswith("link"):
                        entry.external_attr = (0o120777 << 16)
                    zipped.writestr(entry, b"outside")
                with self.assertRaises(capture.package.PackageError):
                    capture.copy_xcresult(outputs, destination)
                self.assertEqual(list(destination.iterdir()), [])

    def test_missing_or_ambiguous_native_bundle_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outputs, destination = root / "outputs", root / "copy"
            outputs.mkdir()
            destination.mkdir()
            with self.assertRaises(capture.package.PackageError):
                capture.copy_xcresult(outputs, destination)
            (outputs / "test.xcresult").mkdir()
            (outputs / "outputs.zip").write_bytes(b"not a valid archive")
            with self.assertRaisesRegex(capture.package.PackageError, "Ambiguous"):
                capture.copy_xcresult(outputs, destination)


class NamedScreenshotTests(unittest.TestCase):
    def setUp(self):
        self.selection = "DraftPocketDemoUITests/testCaptureProductionStory"
        self.case = {"identifier": self.selection + "()", "status": "Success", "summary_ref": "0~summary"}
        self.content = b"\x89PNG\r\n\x1a\nexample payload for mocked native exporter"
        self.attachments = []
        for name, count in capture.SCREENSHOTS["testCaptureProductionStory"].items():
            for _ in range(count):
                self.attachments.append({
                    "name": {"_value": name}, "uniformTypeIdentifier": {"_value": "public.png"},
                    "filename": {"_value": "../../never-use-native-filenames.png"},
                    "payloadRef": {"id": {"_value": f"0~payload-{len(self.attachments)}"}},
                    "payloadSize": {"_value": str(len(self.content))},
                    "timestamp": {"_value": "2026-11-05T10:00:00.000+0000"},
                })
        self.summary = {"identifier": {"_value": "testCaptureProductionStory()"}, "testStatus": {"_value": "Success"},
                        "activitySummaries": {"_values": [{"subactivities": {"_values": [
                            {"attachments": {"_values": self.attachments}}
                        ]}}]}}

    def test_only_explicit_names_are_selected_and_paths_are_generated(self):
        self.attachments.append({"name": {"_value": "private diagnostic screenshot"},
                                 "payloadRef": {"id": {"_value": "0~must-not-export"}}})
        selected = capture.named_screenshots(self.summary, self.selection)
        self.assertEqual(len(selected), 4)
        self.assertEqual(len(set(item["path"] for item in selected)), 4)
        self.assertTrue(all("/" not in item["path"] and ".." not in item["path"] for item in selected))
        self.assertNotIn("0~must-not-export", [item["payload_ref"] for item in selected])

    def test_typed_empty_activities_do_not_mask_named_screenshots(self):
        self.summary["activitySummaries"]["_values"].insert(0, {
            "attachments": {"_type": {"_name": "Array"}},
            "subactivities": {"_type": {"_name": "Array"}}})
        self.assertEqual(len(capture.named_screenshots(self.summary, self.selection)), 4)
        self.summary["activitySummaries"]["_values"][0]["attachments"]["_values"] = None
        with self.assertRaises(capture.package.PackageError):
            capture.named_screenshots(self.summary, self.selection)

    def test_wrong_case_missing_or_duplicate_named_images_rejected(self):
        variants = []
        for key, value in (("identifier", "testCaptureProductionStoryFake()"), ("testStatus", "Failure")):
            summary = copy.deepcopy(self.summary)
            summary[key]["_value"] = value
            variants.append(summary)
        for rows in (self.attachments[:-1], self.attachments + [self.attachments[0]]):
            summary = copy.deepcopy(self.summary)
            summary["activitySummaries"] = {"_values": [{"attachments": {"_values": rows}}]}
            variants.append(summary)
        for summary in variants:
            with self.subTest(summary=summary), self.assertRaises(capture.package.PackageError):
                capture.named_screenshots(summary, self.selection)

    def test_invalid_types_payload_refs_sizes_and_timestamps_rejected(self):
        for key, value in (("uniformTypeIdentifier", "public.plain-text"), ("payloadSize", "0"),
                           ("payloadSize", str(33 * 1024 * 1024)), ("payloadSize", 15),
                           ("timestamp", "2026-11-05T10:00:00"), ("timestamp", "invalid")):
            summary = copy.deepcopy(self.summary)
            summary["activitySummaries"]["_values"][0]["subactivities"]["_values"][0]["attachments"]["_values"][0][key] = {"_value": value}
            with self.subTest(key=key, value=value), self.assertRaises(capture.package.PackageError):
                capture.named_screenshots(summary, self.selection)
        self.attachments[0]["payloadRef"]["id"]["_value"] = "--output-path=/outside"
        with self.assertRaises(capture.package.PackageError):
            capture.named_screenshots(self.summary, self.selection)

    def export_tool(self, command):
        if command[0] == "ffprobe":
            return json.dumps({"streams": [{"codec_type": "video", "codec_name": "png", "nb_read_frames": "1",
                                            "width": 1206, "height": 2622}]}).encode()
        self.assertEqual(command[:7], ["xcrun", "xcresulttool", "export", "object", "--legacy", "--type", "file"])
        Path(command[command.index("--output-path") + 1]).write_bytes(self.content)
        return b""

    def test_export_preserves_bytes_and_native_identity_without_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = Path(temporary)
            with mock.patch.object(capture.package, "run", side_effect=self.export_tool) as run:
                capture.export_named_screenshots(Path("test.xcresult"), artifacts, self.case, self.selection, self.summary)
            self.assertEqual(run.call_count, 8)
            manifest = json.loads((artifacts / "screenshots/manifest.json").read_text())
            self.assertEqual(manifest["native_case"], self.selection)
            self.assertEqual(manifest["summary_ref"], "0~summary")
            self.assertEqual(len(manifest["screenshots"]), 4)
            for item in manifest["screenshots"]:
                self.assertEqual((artifacts / "screenshots" / item["path"]).read_bytes(), self.content)
                self.assertEqual(item["sha256"], hashlib.sha256(self.content).hexdigest())
                self.assertEqual((item["width"], item["height"]), (1206, 2622))
            self.assertEqual(len(list(artifacts.rglob("*"))), 6)

    def test_invalid_export_fails_before_artifact_promotion(self):
        for invalid in (b"not PNG", b"\x89PNG\r\n\x1a\nwrong length"):
            def tool(command):
                Path(command[command.index("--output-path") + 1]).write_bytes(invalid)
                return b""
            with self.subTest(payload=invalid), tempfile.TemporaryDirectory() as temporary:
                artifacts = Path(temporary)
                with mock.patch.object(capture.package, "run", side_effect=tool), self.assertRaises(capture.package.PackageError):
                    capture.export_named_screenshots(Path("test.xcresult"), artifacts, self.case, self.selection, self.summary)
                self.assertEqual(list(artifacts.iterdir()), [])

    def test_non_image_or_multiframe_export_is_not_promoted(self):
        def tool(command):
            if command[0] == "ffprobe":
                return json.dumps({"streams": [{"codec_type": "video", "codec_name": "png", "nb_read_frames": "2",
                                                "width": 1206, "height": 2622}]}).encode()
            return self.export_tool(command)
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = Path(temporary)
            with mock.patch.object(capture.package, "run", side_effect=tool), self.assertRaises(capture.package.PackageError):
                capture.export_named_screenshots(Path("test.xcresult"), artifacts, self.case, self.selection, self.summary)
            self.assertEqual(list(artifacts.iterdir()), [])


class BinaryProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.udid = "CA1C775E-9033-4666-9B09-1E195CA035B5"
        self.application = "A3159F14-508D-4CB9-946D-84ED2F4FEFAE"
        self.bundle_id = "org.example.draftpocket"
        self.target = "//Tests/DraftPocketDemo:DraftPocketDemoUITests"

    def create_runner(self, checkout, artifact, host="Telegram/Telegram.ipa"):
        runner = checkout / "bazel-bin/Tests/DraftPocketDemo/DraftPocketDemoUITests"
        runner.parent.mkdir(parents=True, exist_ok=True)
        runner.write_text('#!/bin/bash\nTEST_HOST_PATH="' + host + '"\n', encoding="utf-8")
        manifest = runner.with_name(runner.name + ".runfiles_manifest")
        manifest.write_text("_main/" + host + " " + str(artifact) + "\n", encoding="utf-8")
        return runner, manifest

    def create_ipa(self, app, ipa):
        ipa.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(ipa, "w") as archive:
            for file in app.rglob("*"):
                if file.is_file():
                    archive.write(file, "Payload/Telegram.app/" + file.relative_to(app).as_posix())

    def create_alias(self, alias, physical):
        if os.name == "nt":
            # Directory junctions need no symbolic-link privilege on Windows.
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                "New-Item -ItemType Junction -Path $env:DRAFT_TEST_ALIAS -Target $env:DRAFT_TEST_PHYSICAL | Out-Null"],
                env={**os.environ, "DRAFT_TEST_ALIAS": str(alias), "DRAFT_TEST_PHYSICAL": str(physical)},
                check=True, capture_output=True)
        else:
            alias.symlink_to(physical, target_is_directory=True)

    def create_app(self, app):
        for prefix, identifier, executable in (("", self.bundle_id, "Telegram"),
                ("Frameworks/TelegramUIFramework.framework", self.bundle_id + ".TelegramUI", "TelegramUIFramework"),
                ("Frameworks/TelegramCoreFramework.framework", self.bundle_id + ".TelegramCore", "TelegramCoreFramework")):
            destination = app / prefix
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": identifier,
                "CFBundleExecutable": executable, "CFBundleSupportedPlatforms": ["iPhoneSimulator"]}))
            (destination / executable).write_bytes(("compiled test fixture " + executable).encode())

    def test_fresh_ipa_and_installed_main_ui_core_files_match_exactly(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "Devices" / self.udid / "data/Containers/Bundle/Application" / self.application / "Telegram.app"
            self.create_app(app)
            checkout = root / "checkout"
            ipa = checkout / "bazel-out/ios_sim_arm64-dbg-transition/bin/Telegram/Telegram.ipa"
            self.create_ipa(app, ipa)
            self.create_runner(checkout, ipa)
            # A stale app-target output must never win over the UI test's host.
            stale = checkout / "bazel-bin/Telegram/Telegram.ipa"
            stale.parent.mkdir()
            stale.write_bytes(b"stale prior app build")
            built = capture.built_binary_inventory(checkout, self.bundle_id, self.target)
            result = capture.verify_installed_binaries(app, root / "Devices", self.udid, self.bundle_id, built)
            self.assertEqual(result["status"], "matched")
            self.assertEqual(len(result["files"]), 6)
            self.assertEqual(result["udid"], self.udid)
            self.assertEqual(result["files"][1]["sha256"], hashlib.sha256((app / "Telegram").read_bytes()).hexdigest())
            self.assertNotIn(str(root), json.dumps(result))
            self.assertEqual(built["source"], "bazel-out/ios_sim_arm64-dbg-transition/bin/Telegram/Telegram.ipa")
            self.assertEqual(built["declared_test_host"], "Telegram/Telegram.ipa")
            self.assertEqual(built["test_target"], self.target)

    def test_device_root_alias_matches_but_inner_device_alias_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            physical = root / "physical"
            physical.mkdir()
            alias = root / "alias"
            self.create_alias(alias, physical)
            devices = physical / "Devices"
            app = devices / self.udid / "data/Containers/Bundle/Application" / self.application / "Telegram.app"
            self.create_app(app)
            aliased_app = alias / app.relative_to(physical)
            # Model /var and /private/var spellings in both directions.
            self.assertEqual(capture.owned_app_container(aliased_app, devices, self.udid), app)
            self.assertEqual(capture.owned_app_container(app, alias / "Devices", self.udid), app)
            other_container = app.parent.with_name("B3159F14-508D-4CB9-946D-84ED2F4FEFAE")
            self.create_alias(other_container, app.parent)
            with self.assertRaises(capture.package.PackageError):
                capture.owned_app_container(other_container / "Telegram.app", devices, self.udid)

    def test_manifest_cannot_select_an_outside_or_ambiguous_test_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            checkout = root / "checkout"
            ipa = checkout / "bazel-out/ios-transition/bin/Telegram/Telegram.ipa"
            ipa.parent.mkdir(parents=True)
            ipa.write_bytes(b"host")
            runner, manifest = self.create_runner(checkout, ipa)
            valid = manifest.read_text(encoding="utf-8")
            outside = root / "outside/Telegram.ipa"
            outside.parent.mkdir()
            outside.write_bytes(b"not this Bazel output")
            for mapping in (valid + valid, "_main/Telegram/Telegram.ipa " + str(outside) + "\n",
                            "_main/Telegram/Telegram.ipa relative/Telegram.ipa\n", "_main/other.ipa " + str(ipa) + "\n"):
                manifest.write_text(mapping, encoding="utf-8")
                with self.subTest(mapping=mapping), self.assertRaises(capture.package.PackageError):
                    capture.declared_test_host(checkout, self.target)
            manifest.write_text(valid, encoding="utf-8")
            for host in ("../Telegram/Telegram.ipa", "$(pwd)/Telegram.ipa", "/Telegram/Telegram.ipa"):
                runner.write_text('TEST_HOST_PATH="' + host + '"\n', encoding="utf-8")
                with self.subTest(host=host), self.assertRaises(capture.package.PackageError):
                    capture.declared_test_host(checkout, self.target)
            runner.write_text('TEST_HOST_PATH="Telegram/Telegram.ipa"\n' * 2, encoding="utf-8")
            with self.assertRaises(capture.package.PackageError):
                capture.declared_test_host(checkout, self.target)

    def test_manifest_paths_with_spaces_and_escaped_records_select_one_exact_app(self):
        with tempfile.TemporaryDirectory(prefix="draft pocket ") as temporary:
            checkout = Path(temporary).resolve() / "checkout"
            app = checkout / "bazel-out/ios-transition/bin/Telegram/Telegram.app"
            self.create_app(app)
            _, manifest = self.create_runner(checkout, app, "Telegram/Telegram.app")
            expected = capture.directory_binary_inventory(app, self.bundle_id)
            escaped_path = str(app).replace("\\", "\\b")
            # Bazel escapes spaces in logical keys, but leaves target spaces
            # literal: consumers split the record only at the first separator.
            auxiliary = " _main/auxiliary\\sdata " + escaped_path + "\n"
            for mapping in (manifest.read_text(encoding="utf-8") + auxiliary,
                            " _main/Telegram/Telegram.app " + escaped_path + "\n",
                            " _main/Telegram/Telegram.app " + escaped_path.replace(" ", "\\s") + "\n"):
                manifest.write_text(mapping, encoding="utf-8")
                built = capture.built_binary_inventory(checkout, self.bundle_id, self.target)
                self.assertEqual(built["files"], expected["files"])

    def test_external_bazel_output_links_use_the_transitioned_declared_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            checkout = root / "checkout"
            checkout.mkdir()
            outputs = root / "external-cache/execroot/_main/bazel-out"
            host_bin = outputs / "darwin_arm64-dbg/bin"
            host_bin.mkdir(parents=True)
            self.create_alias(checkout / "bazel-bin", host_bin)
            self.create_alias(checkout / "bazel-out", outputs)
            app = outputs / "ios_sim_arm64-dbg-transition/bin/Telegram/Telegram.app"
            self.create_app(app)
            self.create_runner(checkout, app, "Telegram/Telegram.app")
            built = capture.built_binary_inventory(checkout, self.bundle_id, self.target)
            self.assertEqual(built["source"], "bazel-out/ios_sim_arm64-dbg-transition/bin/Telegram/Telegram.app")
            self.assertEqual(len(built["files"]), 6)
            self.assertFalse((host_bin / "Telegram").exists())

    def test_manifest_artifact_links_below_output_root_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary).resolve() / "checkout"
            app = checkout / "bazel-out/physical/bin/Telegram/Telegram.app"
            self.create_app(app)
            alias = checkout / "bazel-out/alias"
            self.create_alias(alias, checkout / "bazel-out/physical")
            self.create_runner(checkout, alias / "bin/Telegram/Telegram.app", "Telegram/Telegram.app")
            with self.assertRaises(capture.package.PackageError):
                capture.declared_test_host(checkout, self.target)

    def test_changed_executable_or_metadata_is_not_provenance(self):
        for name in ("Telegram", "Info.plist", "Frameworks/TelegramCoreFramework.framework/TelegramCoreFramework"):
            with self.subTest(path=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                app = root / self.udid / "data/Containers/Bundle/Application" / self.application / "Telegram.app"
                self.create_app(app)
                built = {"source": "test fresh build", **capture.directory_binary_inventory(app, self.bundle_id)}
                target = app / name
                target.write_bytes(target.read_bytes() + b" ")
                with self.assertRaises((capture.package.PackageError, plistlib.InvalidFileException)):
                    capture.verify_installed_binaries(app, root, self.udid, self.bundle_id, built)

    def test_other_device_relative_path_and_nested_app_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for candidate in (Path("relative/Telegram.app"), root / "another-device/Telegram.app",
                              root / self.udid / "data/Containers/Bundle/Application" / self.application / "nested/Telegram.app"):
                with self.subTest(path=candidate), self.assertRaises(capture.package.PackageError):
                    capture.owned_app_container(candidate, root, self.udid)

    def test_bundle_executable_traversal_and_wrong_identity_are_rejected_before_hash(self):
        for executable in ("../outside", "/absolute", "nested/executable", "C:\\outside"):
            with self.subTest(executable=executable):
                metadata = {"CFBundleIdentifier": self.bundle_id, "CFBundleExecutable": executable,
                            "CFBundleSupportedPlatforms": ["iPhoneSimulator"]}
                hash_file = mock.Mock()
                with self.assertRaises(capture.package.PackageError):
                    capture.app_binary_inventory(lambda _: metadata, hash_file, self.bundle_id)
                hash_file.assert_not_called()
        with self.assertRaises(capture.package.PackageError):
            capture.app_binary_inventory(lambda _: {"CFBundleIdentifier": "another.app"}, mock.Mock(), self.bundle_id)

    def test_missing_build_artifact_and_archive_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            outputs = root / "bazel-out/ios-transition/bin/Telegram"
            outputs.mkdir(parents=True)
            self.create_runner(root, outputs / "Telegram.ipa")
            with self.assertRaises(capture.package.PackageError):
                capture.built_binary_inventory(root, self.bundle_id, self.target)
            with zipfile.ZipFile(outputs / "Telegram.ipa", "w") as archive:
                entry = zipfile.ZipInfo("Payload/Telegram.app/Info.plist")
                entry.external_attr = (0o120777 << 16)
                archive.writestr(entry, b"outside")
            with self.assertRaises(capture.package.PackageError):
                capture.built_binary_inventory(root, self.bundle_id, self.target)


class DiagnosticNativeResultTests(unittest.TestCase):
    def setUp(self):
        self.selection = "DraftPocketDemoUITests/testCaptureProductionStory"
        self.root = {"actions": {"_values": [{"_type": {"_name": "ActionRecord"},
                     "actionResult": {"testsRef": {"id": {"_value": "0~tests"}}}}]}}
        self.leaf = {"identifier": {"_value": "testCaptureProductionStory()"},
                     "testStatus": {"_value": "Failure"}, "summaryRef": {"id": {"_value": "0~summary"}}}
        self.group = {"identifier": {"_value": "DraftPocketDemoUITests"}, "subtests": {"_values": [self.leaf]}}
        self.tests = {"summaries": {"_values": [{"testableSummaries": {"_values": [{
            "tests": {"_values": [self.group]}}]}}]}}
        self.content = b"\x89PNG\r\n\x1a\nmock native failure screenshot"
        self.attachment = {"name": {"_value": "demo-entrypoint-failure"},
                           "uniformTypeIdentifier": {"_value": "public.png"},
                           "payloadRef": {"id": {"_value": "0~image"}},
                           "payloadSize": {"_value": str(len(self.content))}}
        self.summary = {"identifier": {"_value": "testCaptureProductionStory()"},
                        "testStatus": {"_value": "Failure"}, "private activity text": "MUST-STAY-PRIVATE",
                        "activitySummaries": {"_values": [{"attachments": {"_type": {"_name": "Array"}}},
                                                          {"attachments": {"_values": [self.attachment]}}]}}
        self.exported = []

    def tool(self, command):
        if command[:3] == ["xcrun", "xcresulttool", "get"]:
            identifier = command[command.index("--id") + 1] if "--id" in command else None
            return json.dumps({None: self.root, "0~tests": self.tests, "0~summary": self.summary}[identifier]).encode()
        if command[0] == "ffprobe":
            return json.dumps({"streams": [{"codec_type": "video", "codec_name": "png", "nb_read_frames": "1",
                                            "width": 1206, "height": 2622}]}).encode()
        self.assertEqual(command[:7], ["xcrun", "xcresulttool", "export", "object", "--legacy", "--type", "file"])
        self.exported.append(command[command.index("--id") + 1])
        Path(command[command.index("--output-path") + 1]).write_bytes(self.content)
        return b""

    def paths(self, temporary):
        root = Path(temporary)
        test_directory, artifacts = root / "test", root / "evidence"
        bundle = test_directory / "test.outputs/test.xcresult"
        bundle.mkdir(parents=True)
        (bundle / "Info.plist").write_bytes(b"private native bundle")
        artifacts.mkdir()
        return test_directory, artifacts

    def test_failed_case_keeps_exact_failure_image_and_native_status(self):
        self.summary["activitySummaries"]["_values"].append({"attachments": {"_values": [{
            "name": {"_value": "unrelated screenshot"}, "payloadRef": {"id": {"_value": "0~never-export"}}
        }]}})
        with tempfile.TemporaryDirectory() as temporary:
            source, artifacts = self.paths(temporary)
            with mock.patch.object(capture.package, "run", side_effect=self.tool):
                cases = capture.native_results(source, artifacts, lambda text: text, self.selection, diagnostic=True)
            self.assertEqual(cases[0]["status"], "Failure")
            with self.assertRaises(capture.package.PackageError):
                capture.selected_native_case(cases, self.selection)
            self.assertEqual(self.exported, ["0~image"])
            manifest = json.loads((artifacts / "failure-screenshots/manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["diagnostic"])
            self.assertEqual(manifest["native_status"], "Failure")
            image = artifacts / "failure-screenshots/demo-entrypoint-failure-01.png"
            self.assertEqual(image.read_bytes(), self.content)
            self.assertEqual(json.loads((artifacts / "native-xctest-cases.json").read_text())[0]["status"], "Failure")
            self.assertTrue((artifacts / "native-xcresult-root.json").is_file())
            self.assertTrue((artifacts / "native-xcresult-tests-00.json").is_file())
            self.assertFalse((artifacts / "test.xcresult").exists())
            self.assertFalse((artifacts / "screenshots").exists())
            for path in artifacts.rglob("*.json"):
                self.assertNotIn("MUST-STAY-PRIVATE", path.read_text(encoding="utf-8"))

    def test_default_success_requirement_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, artifacts = self.paths(temporary)
            with mock.patch.object(capture.package, "run", side_effect=self.tool), self.assertRaises(capture.package.PackageError):
                capture.native_results(source, artifacts, lambda text: text, self.selection)
            self.assertEqual(self.exported, [])
            self.assertTrue((artifacts / "native-xctest-cases.json").is_file())

    def test_wrong_duplicate_case_or_wrong_image_retains_json_but_exports_nothing(self):
        variants = ("wrong-case", "duplicate-case", "wrong-image", "duplicate-image", "wrong-summary-status")
        for variant in variants:
            self.setUp()
            if variant == "wrong-case":
                self.leaf["identifier"]["_value"] = "testOther()"
            elif variant == "duplicate-case":
                self.group["subtests"]["_values"].append(copy.deepcopy(self.leaf))
            elif variant == "wrong-image":
                self.attachment["name"]["_value"] = "private-diagnostics"
            elif variant == "duplicate-image":
                self.summary["activitySummaries"]["_values"][1]["attachments"]["_values"].append(copy.deepcopy(self.attachment))
            else:
                self.summary["testStatus"]["_value"] = "Success"
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as temporary:
                source, artifacts = self.paths(temporary)
                with mock.patch.object(capture.package, "run", side_effect=self.tool), self.assertRaises(capture.package.PackageError):
                    capture.native_results(source, artifacts, lambda text: text, self.selection, diagnostic=True)
                self.assertEqual(self.exported, [])
                self.assertTrue((artifacts / "native-xctest-cases.json").is_file())
                self.assertFalse((artifacts / "failure-screenshots").exists())

    def test_malformed_result_remains_error_with_root_evidence(self):
        self.root["actions"]["_values"] = None
        with tempfile.TemporaryDirectory() as temporary:
            source, artifacts = self.paths(temporary)
            with mock.patch.object(capture.package, "run", side_effect=self.tool), self.assertRaises(capture.package.PackageError):
                capture.native_results(source, artifacts, lambda text: text, self.selection, diagnostic=True)
            self.assertTrue((artifacts / "native-xcresult-root.json").is_file())
            self.assertEqual(self.exported, [])


if __name__ == "__main__":
    unittest.main()
