"""Portable preflight gates expensive work; native/capture failures remain independent."""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("ci_simulator", ROOT / "scripts/ci-simulator.py")
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


class NativeTargetAggregationTests(unittest.TestCase):
    simulator_config = {"device_type": "iPhone 17", "os_version": "27.0"}
    simulator_udid = "7C348C6A-4D11-40A5-84AA-FC69999559CB"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        self.artifacts = self.root / "native-evidence"
        self.trace, self.commands, self.plans = [], [], {}
        self.build_errors, self.validation_errors, self.execution_results, self.lifecycle_errors = {}, {}, {}, {}
        self.booted, self.release_on_cleanup = False, True
        self.session = mock.Mock(udid=self.simulator_udid,
            runtime="com.apple.CoreSimulator.SimRuntime.iOS-27-0", ownership_released=False)
        for name in ("create", "release_build_server", "boot", "reset_after_failure", "cleanup"):
            getattr(self.session, name).side_effect = lambda *args, name=name: self.lifecycle(name)
        self.factory = mock.Mock(return_value=self.session)
        self.validator = mock.Mock(side_effect=self.validate)
        self.runner = mock.Mock(side_effect=self.execute)
        modules = mock.patch.dict(sys.modules, {
            "simulator": mock.Mock(SimulatorSession=self.factory),
            "emitted_test_runner": mock.Mock(validate_emitted_script=self.validator, run_emitted=self.runner)})
        modules.start()
        self.addCleanup(modules.stop)
        emission = mock.patch.object(ci.package, "run", side_effect=self.emit)
        self.emission = emission.start()
        self.addCleanup(emission.stop)

    def lifecycle(self, name):
        self.trace.append(name)
        if name in self.lifecycle_errors:
            raise self.lifecycle_errors[name]
        if name in ("boot", "reset_after_failure"):
            self.booted = True
        if name == "cleanup" and self.release_on_cleanup:
            self.session.ownership_released = True
        return self.simulator_udid if name == "create" else None

    def emit(self, command, **kwargs):
        self.assertFalse(self.booted, "No Make/Bazel invocation may run after Simulator boot")
        self.assertIn("create", self.trace)
        self.assertIn("--scriptPath", command)
        self.assertNotIn("--buildOnly", command)
        self.assertEqual(command[command.index("--simulatorUDID") + 1], self.simulator_udid)
        target = command[command.index("--target") + 1]
        self.commands.append(command)
        self.trace.append(("emit", target))
        script = Path(command[command.index("--scriptPath") + 1])
        self.assertTrue(script.is_absolute())
        self.assertFalse(script.is_relative_to(self.artifacts))
        if os.name == "posix":
            self.assertEqual(script.parent.stat().st_mode & 0o777, 0o700)
        if target in self.build_errors:
            raise self.build_errors[target]
        return b""

    def validate(self, checkout, script, *, target, test_filter, udid):
        self.assertFalse(self.booted)
        self.assertEqual(checkout, self.checkout)
        self.assertEqual(udid, self.simulator_udid)
        self.trace.append(("validate", target))
        if target in self.validation_errors:
            raise self.validation_errors[target]
        output = self.checkout / "verified-outputs" / target.rsplit(":", 1)[1] / "test.outputs"
        plan = mock.Mock(target=target, output_directory=output,
                         identity={"target": target, "test_filter": test_filter, "udid": udid})
        self.plans[target] = plan
        return plan

    def execute(self, plan, artifacts, *, stage, timeout_seconds, on_line):
        self.assertTrue(self.booted)
        self.assertEqual(artifacts, self.artifacts)
        self.assertTrue(stage.startswith("native-"))
        self.assertEqual(timeout_seconds, 600)
        self.trace.append(("test", plan.target))
        outcome = self.execution_results.get(plan.target, (0, False, []))
        if isinstance(outcome, Exception):
            raise outcome
        on_line("native stdout preserved\n")
        code, timed_out, errors = outcome
        return mock.Mock(returncode=code, timed_out=timed_out, supervision_errors=errors,
                         output_directory=plan.output_directory)

    def invoke(self, targets):
        self.output, self.errors = io.StringIO(), io.StringIO()
        with redirect_stdout(self.output), redirect_stderr(self.errors):
            return ci.package.run_native_tests(self.checkout, self.root / "config", self.root / "cache", targets,
                simulator_config=self.simulator_config, artifacts=self.artifacts)

    def report(self):
        return json.loads((self.artifacts / "native-target-results.json").read_text())

    def test_all_five_emit_before_boot_and_independent_failures_are_retained(self):
        targets = ["//Tests:One", "//Tests:Two", {"target": "//Tests:Three", "filter": "SelectedTests"},
                   "//Tests:Four", "//Tests:Five"]
        self.build_errors["//Tests:Two"] = ci.package.PackageError("compiler exited 1")
        self.execution_results["//Tests:Four"] = OSError("runner failed to start")
        with self.assertRaises(ci.package.PackageError) as caught:
            self.invoke(targets)
        self.assertEqual(self.trace, ["create", ("emit", "//Tests:One"), ("validate", "//Tests:One"),
            ("emit", "//Tests:Two"), ("emit", "//Tests:Three"), ("validate", "//Tests:Three"),
            ("emit", "//Tests:Four"), ("validate", "//Tests:Four"), ("emit", "//Tests:Five"), ("validate", "//Tests:Five"),
            "release_build_server", "boot", ("test", "//Tests:One"), ("test", "//Tests:Three"),
            ("test", "//Tests:Four"), "reset_after_failure", ("test", "//Tests:Five"), "cleanup"])
        self.assertIn("2 of 5", str(caught.exception))
        self.assertIn("//Tests:Two [build]: compiler exited 1", str(caught.exception))
        self.assertIn("//Tests:Four [test]: runner failed to start", str(caught.exception))
        self.assertEqual(self.commands[2][-2:], ["--testFilter", "SelectedTests"])
        self.assertEqual(self.plans["//Tests:Three"].identity["test_filter"], "SelectedTests")
        report = self.report()
        self.assertEqual([item["test"] for item in report["targets"]], ["passed", "not_run", "passed", "failed", "passed"])
        self.assertEqual(report["simulator"]["udid"], self.simulator_udid)
        self.assertEqual(report["lifecycle_errors"], [])
        self.assertTrue(report["ownership_released"])
        self.assertIn("native stdout preserved", self.output.getvalue())
        self.factory.assert_called_once_with(self.artifacts, label="native", **self.simulator_config)

    def test_all_emission_failures_clean_created_unbooted_device(self):
        self.build_errors = {target: ci.package.PackageError("build failed") for target in ("//Tests:One", "//Tests:Two")}
        with self.assertRaisesRegex(ci.package.PackageError, "2 of 2"):
            self.invoke(list(self.build_errors))
        self.session.create.assert_called_once()
        self.session.release_build_server.assert_not_called()
        self.session.boot.assert_not_called()
        self.session.cleanup.assert_called_once()
        self.runner.assert_not_called()
        self.assertEqual([item["build"] for item in self.report()["targets"]], ["failed", "failed"])

    def test_invalid_emitted_identity_cannot_become_runnable(self):
        self.validation_errors["//Tests:One"] = ci.package.PackageError("stale or wrong runner")
        with self.assertRaisesRegex(ci.package.PackageError, "stale or wrong runner"):
            self.invoke(["//Tests:One", "//Tests:Two"])
        self.assertEqual([call.args[0].target for call in self.runner.call_args_list], ["//Tests:Two"])
        self.assertEqual(self.report()["targets"][0]["build"], "failed")

    def test_creation_failure_prevents_emission_and_still_attempts_cleanup(self):
        self.lifecycle_errors["create"] = ci.package.PackageError("creation identity unavailable")
        with self.assertRaisesRegex(ci.package.PackageError, "creation identity unavailable"):
            self.invoke(["//Tests:One"])
        self.emission.assert_not_called()
        self.runner.assert_not_called()
        self.session.cleanup.assert_called_once()
        self.assertEqual(self.report()["targets"][0]["build"], "not_run")

    def test_boot_failure_cleans_owned_device_and_runs_no_test(self):
        self.lifecycle_errors["boot"] = ci.package.PackageError("boot readiness timed out")
        with self.assertRaisesRegex(ci.package.PackageError, "1 compiled targets did not run"):
            self.invoke(["//Tests:One"])
        self.emission.assert_called_once()
        self.runner.assert_not_called()
        self.session.cleanup.assert_called_once()
        self.assertEqual(self.report()["targets"][0]["test"], "not_run")
        self.assertIn("boot readiness timed out", self.report()["lifecycle_errors"][0])

    def test_release_failure_cleans_already_created_device_without_boot_or_tests(self):
        self.lifecycle_errors["release_build_server"] = ci.package.PackageError("Bazel server busy")
        with self.assertRaisesRegex(ci.package.PackageError, "Bazel server busy"):
            self.invoke(["//Tests:One"])
        self.emission.assert_called_once()
        self.session.create.assert_called_once()
        self.session.boot.assert_not_called()
        self.runner.assert_not_called()
        self.session.cleanup.assert_called_once()

    def test_native_failure_and_cleanup_failure_are_both_retained(self):
        self.lifecycle_errors["cleanup"] = ci.package.PackageError("owned shutdown/delete failed")
        self.execution_results["//Tests:One"] = (7, False, [])
        with self.assertRaises(ci.package.PackageError) as caught:
            self.invoke(["//Tests:One"])
        self.assertIn("runner exited 7", str(caught.exception))
        self.assertIn("owned shutdown/delete failed", str(caught.exception))
        self.session.reset_after_failure.assert_not_called()
        self.session.cleanup.assert_called_once()
        self.assertEqual(self.report()["targets"][0]["test"], "failed")
        self.assertFalse(self.report()["ownership_released"])

    def test_failed_recovery_stops_following_tests_and_still_cleans_up(self):
        self.lifecycle_errors["reset_after_failure"] = ci.package.PackageError("reboot failed")
        self.execution_results["//Tests:One"] = ci.package.PackageError("native crash")
        with self.assertRaises(ci.package.PackageError) as caught:
            self.invoke(["//Tests:One", "//Tests:Two"])
        self.assertEqual(self.emission.call_count, 2)
        self.runner.assert_called_once()
        self.assertIn("native crash", str(caught.exception))
        self.assertIn("reboot failed", str(caught.exception))
        self.assertEqual([item["test"] for item in self.report()["targets"]], ["failed", "not_run"])
        self.session.cleanup.assert_called_once()

    def assert_supervision_stops_following_native_target(self, outcome):
        self.execution_results["//Tests:Uncertain"] = outcome
        with self.assertRaisesRegex(ci.package.PackageError, "1 of 2"):
            self.invoke(["//Tests:Uncertain", "//Tests:Following"])
        report = self.report()
        self.assertEqual([item["test"] for item in report["targets"]], ["failed", "not_run"])
        self.assertEqual(report["targets"][0]["execution"]["returncode"], 0)
        self.assertEqual(report["targets"][0]["execution"]["timed_out"], outcome[1])
        self.assertEqual(report["targets"][0]["execution"]["supervision_errors"], outcome[2])
        self.assertNotIn("Native test target PASSED", self.output.getvalue())
        self.assertTrue(report["lifecycle_errors"])
        self.assertEqual(self.emission.call_count, 2)
        self.runner.assert_called_once()
        self.session.reset_after_failure.assert_not_called()
        self.session.cleanup.assert_called_once()

    def test_zero_exit_with_deadline_stops_before_next_native_target(self):
        self.assert_supervision_stops_following_native_target((0, True, []))

    def test_zero_exit_with_unconfirmed_group_stops_before_next_native_target(self):
        self.assert_supervision_stops_following_native_target((0, False, ["owned group did not drain"]))

    def test_success_still_requires_owned_device_cleanup(self):
        self.invoke(["//Tests:One", "//Tests:Two"])
        self.session.create.assert_called_once()
        self.session.boot.assert_called_once()
        self.session.reset_after_failure.assert_not_called()
        self.session.cleanup.assert_called_once()
        self.assertEqual([item["test"] for item in self.report()["targets"]], ["passed", "passed"])
        self.assertTrue(self.report()["ownership_released"])

    def test_silent_unconfirmed_cleanup_is_not_success(self):
        self.release_on_cleanup = False
        with self.assertRaisesRegex(ci.package.PackageError, "cleanup was not confirmed"):
            self.invoke(["//Tests:One"])
        self.assertFalse(self.report()["ownership_released"])

    def test_empty_invalid_or_duplicate_inventory_starts_no_target(self):
        inventories = ([], ["//Tests:Valid", {"target": "//Tests:Bad", "filter": "unsafe argument"}],
                       ["//Tests:Same", {"target": "//Tests:Same", "filter": "Other"}])
        for targets in inventories:
            with self.subTest(targets=targets), self.assertRaises(ci.package.PackageError):
                self.invoke(targets)
        self.emission.assert_not_called()
        self.factory.assert_not_called()

    def test_missing_pinned_simulator_starts_no_build_or_device(self):
        with self.assertRaisesRegex(ci.package.PackageError, "native_test_simulator"):
            ci.package.run_native_tests(self.checkout, Path("config"), Path("cache"), ["//Tests:One"],
                                        artifacts=self.artifacts)
        self.emission.assert_not_called()
        self.factory.assert_not_called()

    def test_real_cli_catches_shared_helper_error_and_preserves_lifecycle_report(self):
        # A fresh Python runs the actual __main__ guard and native CLI path. The
        # real Simulator helper raises its own imported package.PackageError;
        # only expensive build/toolchain work and subprocess I/O are stubbed.
        script = r'''
import runpy
import subprocess
import sys
import unittest
from pathlib import Path

entry_path, artifacts = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(entry_path.parent))
class ConfigurePortableCLI(unittest.TestCase):
    def runTest(self):
        entry = sys.modules["__main__"]
        entry.validate_package = lambda *args, **kwargs: ({}, {})
        entry.toolchain = lambda *args, **kwargs: {
            "native_test_targets": ["//Tests:One"],
            "native_test_simulator": {"device_type": "iPhone 17", "os_version": "27.0"}}
        entry.run = lambda *args, **kwargs: b""
        import simulator
        simulator.SimulatorSession.release_build_server = lambda *args: None
        simulator.subprocess.run = lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, stdout=b"synthetic runtime discovery error\n")

unittest.defaultTestLoader.discover = lambda *args, **kwargs: unittest.TestSuite([ConfigurePortableCLI()])
sys.argv = [str(entry_path), "test", "--native", "--checkout", str(artifacts / "checkout"),
            "--configuration", str(artifacts / "config"), "--cache", str(artifacts / "cache"),
            "--artifacts", str(artifacts)]
runpy.run_path(str(entry_path), run_name="__main__")
'''
        result = subprocess.run([sys.executable, "-c", script, str(ROOT / "scripts/package.py"), str(self.artifacts)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("Simulator runtimes exited 1", result.stderr)
        report = self.report()
        self.assertEqual(report["targets"][0]["build"], "not_run")
        self.assertEqual(report["targets"][0]["test"], "not_run")
        self.assertIn("Simulator runtimes exited 1", report["lifecycle_errors"][0])


class SimulatorFlowTests(unittest.TestCase):
    def attempt(self, results, with_demo=True):
        """Exercise main's real status/log/timing writes with synthetic processes."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        artifacts = root / "evidence"
        config = json.loads((ROOT / "Integration/build-configuration.example.json").read_text())
        config.update(api_id="123456", api_hash="a" * 32)
        secret_json = json.dumps(config)
        base = {"commit": "b" * 40, "versions": {"macos": "27", "xcode": "27.1"},
                "native_test_simulator": {"device_type": "iPhone 17", "os_version": "27.0"}}
        manifest = {"feature": {"patch": {"sha256": "c" * 64}},
                    "demo": {"available": with_demo, "patch": {"sha256": "d" * 64},
                             "device_type": "iPhone 17", "os_version": "27.0"}}
        visited = []
        self.guard_instances = {}

        def disk_guard(artifacts_path, name, **kwargs):
            self.assertEqual(artifacts_path, artifacts)
            self.assertEqual(kwargs, {"minimum_free_bytes": 1} if name == "native-case-review" else {})
            guard = mock.Mock()
            if results.get(name + "-disk-before"):
                guard.preflight.side_effect = ci.DiskGuardError("Disk reserve exhausted before " + name)
            if results.get(name + "-disk-after"):
                guard.raise_if_failed.side_effect = ci.DiskGuardError("Disk reserve exhausted during " + name)
            self.guard_instances[name] = guard
            return guard

        def start(command, **kwargs):
            self.assertNotIn("DRAFT_POCKET_BUILD_CONFIGURATION_JSON", os.environ)
            self.assertIs(kwargs["start_new_session"], True)
            script = Path(command[1]).name
            name = "capture" if script == "capture-demo.py" else "native-case-review" if script == "native_evidence.py" else command[2]
            if name == "test":
                name = "native-tests" if "--native" in command else "portable-tests"
            if name in ("build", "capture", "native-tests"):
                self.assertIn("--no-disk-cache", command)
                self.assertNotIn("--cache", command)
            visited.append(name)
            if name == "capture" and not results.get("capture-cleanup-missing"):
                take = artifacts / "story"
                take.mkdir()
                evidence = {"schema_version": 1, "label": "capture",
                            "udid": "CA1C775E-9033-4666-9B09-1E195CA035B5",
                            "ownership_released": not results.get("capture-cleanup-unconfirmed", False)}
                (take / "simulator-lifecycle.json").write_text(json.dumps(evidence), encoding="utf-8")
                identity = {"ownership_released": evidence["ownership_released"], "udid": evidence["udid"]}
                if results.get("capture-execution-proof"):
                    runner = {"source": "bazel-9.2-run-script", "returncode": results.get("capture", 0),
                              "udid": evidence["udid"], "test_filter": "DraftPocketDemoUITests/testCaptureProductionStory",
                              "timed_out": False, "supervision_errors": []}
                    identity.update(xctest_exit_code=runner["returncode"], xctest_timed_out=False,
                                    xctest_supervision_errors=[], test_filter=runner["test_filter"])
                    if results.get("capture-group-uncertain"):
                        runner["supervision_errors"] = ["Owned group drain could not be confirmed"]
                        identity["xctest_supervision_errors"] = runner["supervision_errors"]
                    if results.get("capture-exit-missing"):
                        identity["xctest_exit_code"] = None
                    if results.get("capture-exit-omitted"):
                        del identity["xctest_exit_code"]
                    if results.get("capture-proof-identity-missing"):
                        for value in (evidence, identity, runner):
                            value.pop("udid", None)
                            value.pop("test_filter", None)
                        (take / "simulator-lifecycle.json").write_text(json.dumps(evidence), encoding="utf-8")
                    if results.get("capture-runner-boolean-exit"):
                        runner["returncode"] = bool(runner["returncode"])
                    if not results.get("capture-runner-proof-missing"):
                        (take / "emitted-demo-xctest.json").write_text(json.dumps(runner), encoding="utf-8")
                (take / "capture-identity.json").write_text(json.dumps(identity), encoding="utf-8")
                if results.get("qa-cleanup-unconfirmed"):
                    qa = artifacts / "qa"
                    qa.mkdir()
                    evidence["ownership_released"] = False
                    (qa / "simulator-lifecycle.json").write_text(json.dumps(evidence), encoding="utf-8")
                    (qa / "capture-identity.json").write_text(json.dumps(identity), encoding="utf-8")
            # Before configuration parsing only the raw secret is in the redaction list.
            # Children never receive it; this synthetic output verifies that early logs still
            # redact an accidental complete-secret echo without requiring parsed credentials.
            echoed_secret = secret_json if name == "portable-tests" else config["api_hash"]
            def output():
                yield f"{name} synthetic output {echoed_secret}\n"
                if results.get(name + "-interrupt"):
                    raise KeyboardInterrupt()
                if results.get(name + "-stream-error"):
                    raise OSError("synthetic output read failed")
            return mock.Mock(stdout=output(),
                             wait=mock.Mock(return_value=results.get(name, 0)), poll=mock.Mock(return_value=0))

        def tool(command, **kwargs):
            if command[0] == "sw_vers":
                return b"27.0.1\n"
            if command[0] == "xcodebuild":
                return b"Xcode 27.1\nBuild version synthetic\n"
            if command[:4] == ["xcrun", "simctl", "list", "runtimes"]:
                self.assertEqual(kwargs.get("timeout"), 30)
                if results.get("inventory-timeout"):
                    raise ci.package.PackageError("xcrun exceeded 30s")
                return json.dumps(results.get("runtime_info", {"runtimes": [{"name": "iOS 27.0", "version": "27.0",
                    "identifier": "synthetic-runtime", "isAvailable": True,
                    "supportedDeviceTypes": [{"identifier": "synthetic-device"}]}]})).encode()
            if command[:4] == ["xcrun", "simctl", "list", "devicetypes"]:
                self.assertEqual(kwargs.get("timeout"), 30)
                return json.dumps(results.get("device_info", {"devicetypes": [
                    {"name": "iPhone 17", "identifier": "synthetic-device"}]})).encode()
            if command[0] in ("ffmpeg", "ffprobe"):
                return b"synthetic media tool version\n"
            raise AssertionError(f"Unexpected external command: {command}")

        original_is_dir = Path.is_dir

        def is_dir(path):
            return True if path.name == "Developer" and "Xcode_27.1.app" in str(path) else original_is_dir(path)

        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {"RUNNER_TEMP": str(root),
                "DRAFT_POCKET_BUILD_CONFIGURATION_JSON": secret_json,
                "GITHUB_STEP_SUMMARY": str(root / "summary.md")}, clear=True))
            stack.enter_context(mock.patch.object(ci.package, "validate_package", return_value=(base, manifest)))
            stack.enter_context(mock.patch.object(ci.package, "run", side_effect=tool))
            stack.enter_context(mock.patch.object(ci.subprocess, "Popen", side_effect=start))
            stack.enter_context(mock.patch.object(ci, "DiskGuard", side_effect=disk_guard))
            stack.enter_context(mock.patch.object(ci.platform, "system", return_value="Darwin"))
            stack.enter_context(mock.patch.object(ci.platform, "machine", return_value="arm64"))
            stack.enter_context(mock.patch.object(Path, "is_dir", is_dir))
            stack.enter_context(mock.patch.object(ci.shutil, "which", return_value="synthetic-tool"))
            stack.enter_context(redirect_stdout(io.StringIO()))
            stack.enter_context(redirect_stderr(io.StringIO()))
            code = ci.main(["--artifacts", str(artifacts)])
        self.assertFalse((root / "draft-pocket-build-configuration.json").exists())
        for log in artifacts.glob("*.log"):
            self.assertNotIn(config["api_hash"], log.read_text())
        return code, json.loads((artifacts / "status.json").read_text()), artifacts, visited

    def test_native_failure_preserves_completed_capture_and_exit_stays_failed(self):
        code, status, artifacts, visited = self.attempt({"native-tests": 1})
        self.assertEqual(code, 1)
        self.assertEqual(visited, ["portable-tests", "bootstrap", "check", "build", "capture", "native-tests", "native-case-review"])
        self.assertEqual(status["stages"]["portable_tests"], "passed")
        self.assertEqual(status["stages"]["build"], "passed")
        self.assertEqual(status["stages"]["native_tests"], "failed")
        self.assertEqual(status["stages"]["capture"], "passed")
        self.assertEqual(set(status["failures"]), {"native_tests"})
        self.assertEqual(set(status["stage_times_utc"]["native_tests"]), {"running", "failed"})
        self.assertEqual(set(status["stage_times_utc"]["capture"]), {"running", "passed"})
        self.assertTrue((artifacts / "native-tests.log").is_file())
        self.assertTrue((artifacts / "capture.log").is_file())

    def test_both_failures_and_their_timestamps_survive(self):
        code, status, artifacts, visited = self.attempt({"native-tests": 1, "capture": 2})
        self.assertEqual(code, 1)
        self.assertEqual(set(status["failures"]), {"native_tests", "capture"})
        for stage in ("native_tests", "capture"):
            self.assertEqual(status["stages"][stage], "failed")
            self.assertEqual(set(status["stage_times_utc"][stage]), {"running", "failed"})
            self.assertIn(stage + ":", (artifacts / "failure.txt").read_text())
            self.assertTrue((artifacts / (stage + "-failure.txt")).is_file())
        self.assertEqual(visited[-1], "native-case-review")

    def test_capture_failure_still_runs_native_tests(self):
        code, status, artifacts, visited = self.attempt({"capture": 1})
        self.assertEqual(code, 1)
        self.assertEqual(visited, ["portable-tests", "bootstrap", "check", "build", "capture", "native-tests", "native-case-review"])
        self.assertEqual(status["stages"]["capture"], "failed")
        self.assertEqual(status["stages"]["native_tests"], "passed")
        self.assertEqual(set(status["failures"]), {"capture"})
        self.assertTrue((artifacts / "capture.log").is_file())
        self.assertTrue((artifacts / "native-tests.log").is_file())

    def test_unconfirmed_or_missing_capture_cleanup_never_starts_another_device(self):
        for variant in ("capture-cleanup-missing", "capture-cleanup-unconfirmed", "qa-cleanup-unconfirmed"):
            for capture_exit in (0, 1):
                with self.subTest(variant=variant, capture_exit=capture_exit):
                    code, status, artifacts, visited = self.attempt({variant: True, "capture": capture_exit})
                    self.assertEqual(code, 1)
                    self.assertEqual(visited[-1], "capture")
                    self.assertEqual(status["stages"]["native_tests"], "not_run")
                    self.assertIs(status["capture_cleanup_confirmed"], False)
                    self.assertIn("cleanup was not confirmed", status["native_tests_skip_reason"])
                    self.assertFalse((artifacts / "native-tests.log").exists())
                    if capture_exit:
                        self.assertIn("capture exited 1", status["failures"]["capture"])

    def test_cleanup_proof_requires_typed_boolean_and_expected_session(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = Path(directory)
            take = artifacts / "story"
            take.mkdir()
            path = take / "simulator-lifecycle.json"
            for evidence in ([], {}, {"schema_version": 1, "label": "capture", "ownership_released": "true"},
                             {"schema_version": 1, "label": "native", "ownership_released": True}):
                with self.subTest(evidence=evidence):
                    path.write_text(json.dumps(evidence), encoding="utf-8")
                    self.assertFalse(ci.capture_cleanup_confirmed(artifacts))
            path.write_text("not json", encoding="utf-8")
            self.assertFalse(ci.capture_cleanup_confirmed(artifacts))

    def test_device_deletion_cannot_override_uncertain_or_missing_runner_cleanup(self):
        for variant in ("capture-group-uncertain", "capture-exit-missing", "capture-runner-proof-missing",
                        "capture-exit-omitted", "capture-proof-identity-missing", "capture-runner-boolean-exit"):
            with self.subTest(variant=variant):
                code, status, _, visited = self.attempt({"capture-execution-proof": True, variant: True})
                self.assertEqual(code, 1)
                self.assertEqual(visited[-1], "capture")
                self.assertEqual(status["stages"]["native_tests"], "not_run")
                self.assertIs(status["capture_cleanup_confirmed"], False)

    def test_failed_native_capture_case_with_confirmed_cleanup_allows_remaining_lane(self):
        code, status, _, visited = self.attempt({"capture-execution-proof": True, "capture": 1})
        self.assertEqual(code, 1)
        self.assertIs(status["capture_cleanup_confirmed"], True)
        self.assertEqual(status["stages"]["capture"], "failed")
        self.assertEqual(status["stages"]["native_tests"], "passed")
        self.assertEqual(visited[-2:], ["native-tests", "native-case-review"])

    def test_failed_app_build_prevents_tests_and_capture(self):
        code, status, _, visited = self.attempt({"build": 1})
        self.assertEqual(code, 1)
        self.assertEqual(visited, ["portable-tests", "bootstrap", "check", "build"])
        self.assertEqual(status["stages"]["native_tests"], "not_run")
        self.assertEqual(status["stages"]["capture"], "not_run")
        self.assertEqual(set(status["failures"]), {"build"})

    def test_portable_failure_stops_before_toolchain_reconstruction_build_and_capture(self):
        code, status, artifacts, visited = self.attempt({"portable-tests": 1})
        self.assertEqual(code, 1)
        self.assertEqual(visited, ["portable-tests"])
        self.assertEqual(status["stages"]["package_integrity"], "passed")
        self.assertEqual(status["stages"]["portable_tests"], "failed")
        for stage in ("toolchain", "configuration", "reconstruction", "build", "native_tests", "capture"):
            self.assertEqual(status["stages"][stage], "not_run")
            self.assertNotIn(stage, status["stage_times_utc"])
        self.assertEqual(set(status["failures"]), {"portable_tests"})
        self.assertEqual(set(status["stage_times_utc"]["portable_tests"]), {"running", "failed"})
        self.assertTrue((artifacts / "portable-tests.log").is_file())
        self.assertIn("[REDACTED]", (artifacts / "portable-tests.log").read_text())
        self.assertFalse((artifacts / "build.log").exists())
        self.assertFalse((artifacts / "native-tests.log").exists())
        self.assertFalse((artifacts / "capture.log").exists())

    def test_all_success_has_no_failure_artifact(self):
        code, status, artifacts, visited = self.attempt({})
        self.assertEqual(code, 0)
        self.assertTrue(all(value == "passed" for value in status["stages"].values()))
        self.assertNotIn("failures", status)
        self.assertFalse((artifacts / "failure.txt").exists())
        self.assertEqual(visited, ["portable-tests", "bootstrap", "check", "build", "capture", "native-tests", "native-case-review"])
        preflight = json.loads((artifacts / "simulator-preflight.json").read_text())
        self.assertEqual(preflight["status"], "passed")
        self.assertEqual([entry["lane"] for entry in preflight["selections"]], ["native", "capture"])
        self.assertTrue(all(entry["os_version"] == "27.0" and entry["device_type"] == "iPhone 17"
                            for entry in preflight["selections"]))

    def test_exact_simulator_failure_stops_before_configuration_and_expensive_work(self):
        variants = [
            {"runtime_info": {"runtimes": [{"name": "iOS 27.1", "version": "27.1", "isAvailable": True,
                                            "identifier": "other-runtime"}]}},
            {"device_info": {"devicetypes": []}},
            {"runtime_info": {"runtimes": [{"name": "iOS 27.0", "version": "27.0", "isAvailable": True,
                "identifier": "synthetic-runtime", "supportedDeviceTypes": [{"identifier": "other-device"}]}]}},
            {"inventory-timeout": True},
        ]
        for results in variants:
            with self.subTest(results=results):
                code, status, artifacts, visited = self.attempt(results)
                self.assertEqual(code, 1)
                self.assertEqual(visited, ["portable-tests"])
                self.assertEqual(status["stages"]["toolchain"], "failed")
                for name in ("configuration", "reconstruction", "build", "capture", "native_tests"):
                    self.assertEqual(status["stages"][name], "not_run")
                self.assertIn("Exact Simulator preflight failed", status["failure"])
                evidence = json.loads((artifacts / "simulator-preflight.json").read_text())
                self.assertEqual(evidence["status"], "failed")
                self.assertTrue(evidence["error"])

    def test_native_exit_success_cannot_override_incomplete_case_evidence(self):
        code, status, artifacts, visited = self.attempt({"native-case-review": 1})
        self.assertEqual(code, 1)
        self.assertEqual(visited[-2:], ["native-tests", "native-case-review"])
        self.assertEqual(status["stages"]["capture"], "passed")
        self.assertEqual(status["stages"]["native_tests"], "failed")
        self.assertIn("native-case-review exited 1", status["failures"]["native_tests"])
        self.assertTrue((artifacts / "native-case-review.log").is_file())

    def test_native_and_case_review_failures_are_both_preserved(self):
        code, status, artifacts, visited = self.attempt({"native-tests": 1, "native-case-review": 1})
        self.assertEqual(code, 1)
        self.assertEqual(visited[-2:], ["native-tests", "native-case-review"])
        self.assertIn("native-tests exited 1", status["failures"]["native_tests"])
        self.assertIn("native-case-review exited 1", status["failures"]["native_tests"])
        self.assertTrue((artifacts / "native-tests.log").is_file())
        self.assertTrue((artifacts / "native-case-review.log").is_file())

    def test_low_disk_prevents_build_start_and_preserves_stage_failure(self):
        code, status, artifacts, visited = self.attempt({"build-disk-before": 1})
        self.assertEqual(code, 1)
        self.assertEqual(visited, ["portable-tests", "bootstrap", "check"])
        self.assertEqual(status["stages"]["build"], "failed")
        self.assertEqual(status["stages"]["capture"], "not_run")
        self.assertIn("Disk reserve exhausted before build", (artifacts / "failure.txt").read_text())
        self.guard_instances["build"].start.assert_not_called()
        self.guard_instances["build"].stop.assert_called_once_with(terminate=False)

    def test_low_disk_during_native_cannot_pass_and_case_review_still_runs(self):
        code, status, artifacts, visited = self.attempt({"native-tests-disk-after": 1})
        self.assertEqual(code, 1)
        self.assertEqual(visited[-2:], ["native-tests", "native-case-review"])
        self.assertEqual(status["stages"]["capture"], "passed")
        self.assertEqual(status["stages"]["native_tests"], "failed")
        self.assertIn("Disk reserve exhausted during native-tests", status["failures"]["native_tests"])
        self.guard_instances["native-tests"].stop.assert_called_once_with(terminate=False)
        self.assertTrue((artifacts / "native-case-review.log").is_file())

    def test_stream_error_cleans_owned_group_even_if_wrapper_already_exited(self):
        code, status, _, visited = self.attempt({"capture-stream-error": 1})
        self.assertEqual(code, 1)
        self.assertEqual(status["stages"]["capture"], "failed")
        self.assertIn("synthetic output read failed", status["failures"]["capture"])
        self.guard_instances["capture"].stop.assert_called_once_with(terminate=True)
        self.assertEqual(visited[-2:], ["native-tests", "native-case-review"])

    def test_interrupt_cleans_owned_group_and_remains_an_interrupt(self):
        with self.assertRaises(KeyboardInterrupt):
            self.attempt({"capture-interrupt": 1})
        self.guard_instances["capture"].stop.assert_called_once_with(terminate=True)
        self.assertNotIn("native-tests", self.guard_instances)

    def test_missing_demo_is_preserved_alongside_native_failure(self):
        code, status, _, visited = self.attempt({"native-tests": 1}, with_demo=False)
        self.assertEqual(code, 1)
        self.assertEqual(set(status["failures"]), {"native_tests", "capture"})
        self.assertNotIn("capture", visited)
        self.assertIn("no isolated full-client", status["failures"]["capture"])


class CommandTimeoutTests(unittest.TestCase):
    def test_bounded_public_probe_timeout_is_a_package_failure(self):
        with self.assertRaisesRegex(ci.package.PackageError, "exceeded 0.1s"):
            ci.package.run([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.1)


if __name__ == "__main__":
    unittest.main()
