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
        self.artifacts = Path(temporary.name) / "native-evidence"
        self.session = mock.Mock(udid=self.simulator_udid, runtime="com.apple.CoreSimulator.SimRuntime.iOS-27-0")
        self.factory = mock.Mock(return_value=self.session)
        patch = mock.patch.dict(sys.modules, {"simulator": mock.Mock(SimulatorSession=self.factory)})
        patch.start()
        self.addCleanup(patch.stop)

    def invoke(self, targets):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return ci.package.run_native_tests(Path("checkout"), Path("config"), Path("cache"), targets,
                simulator_config=self.simulator_config, artifacts=self.artifacts)

    def report(self):
        return json.loads((self.artifacts / "native-target-results.json").read_text())

    def test_every_target_runs_and_all_failures_are_reported(self):
        targets = ["//Tests:One", "//Tests:Two", {"target": "//Tests:Three", "filter": "SelectedTests"},
                   "//Tests:Four", "//Tests:Five"]
        visited = []
        for method in ("release_build_server", "create", "boot", "reset_after_failure", "cleanup"):
            getattr(self.session, method).side_effect = lambda *args, name=method: visited.append(name)

        def execute(command, **kwargs):
            target = command[command.index("--target") + 1]
            building = "--buildOnly" in command
            visited.append(("build" if building else "test", target))
            if building and target == "//Tests:Two":
                raise ci.package.PackageError("compiler exited 1")
            if not building and target == "//Tests:Four":
                raise OSError("runner failed to start")

        with mock.patch.object(ci.package, "run", side_effect=execute) as run:
            with self.assertRaises(ci.package.PackageError) as caught:
                self.invoke(targets)
        self.assertEqual(visited, [("build", "//Tests:" + name) for name in ("One", "Two", "Three", "Four", "Five")] +
                         ["release_build_server", "create", "boot", ("test", "//Tests:One"), ("test", "//Tests:Three"),
                          ("test", "//Tests:Four"), "reset_after_failure", ("test", "//Tests:Five"), "cleanup"])
        self.assertIn("2 of 5", str(caught.exception))
        self.assertIn("//Tests:Two [build]: compiler exited 1", str(caught.exception))
        self.assertIn("//Tests:Four [test]: runner failed to start", str(caught.exception))
        test_commands = [call.args[0] for call in run.call_args_list if "--buildOnly" not in call.args[0]]
        for command in test_commands:
            self.assertEqual(command[command.index("--simulatorUDID") + 1], self.simulator_udid)
        self.assertEqual(test_commands[1][-2:], ["--testFilter", "SelectedTests"])
        report = self.report()
        self.assertEqual([item["test"] for item in report["targets"]], ["passed", "not_run", "passed", "failed", "passed"])
        self.assertEqual(report["simulator"]["udid"], self.simulator_udid)
        self.assertEqual(report["lifecycle_errors"], [])
        self.factory.assert_called_once_with(self.artifacts, label="native", **self.simulator_config)

    def test_all_build_failures_create_no_simulator(self):
        with mock.patch.object(ci.package, "run", side_effect=ci.package.PackageError("build failed")):
            with self.assertRaisesRegex(ci.package.PackageError, "2 of 2"):
                self.invoke(["//Tests:One", "//Tests:Two"])
        self.factory.assert_not_called()
        self.assertEqual([item["build"] for item in self.report()["targets"]], ["failed", "failed"])

    def test_boot_failure_cleans_up_owned_device_and_runs_no_test(self):
        self.session.boot.side_effect = ci.package.PackageError("boot readiness timed out")
        with mock.patch.object(ci.package, "run") as run:
            with self.assertRaisesRegex(ci.package.PackageError, "1 compiled targets did not run"):
                self.invoke(["//Tests:One"])
        self.assertEqual(run.call_count, 1)
        self.assertIn("--buildOnly", run.call_args.args[0])
        self.session.cleanup.assert_called_once()
        self.assertEqual(self.report()["targets"][0]["test"], "not_run")
        self.assertIn("boot readiness timed out", self.report()["lifecycle_errors"][0])

    def test_server_release_failure_prevents_device_and_tests_but_preserves_failure(self):
        self.session.release_build_server.side_effect = ci.package.PackageError("Bazel server busy")
        with mock.patch.object(ci.package, "run") as run, self.assertRaisesRegex(ci.package.PackageError, "Bazel server busy"):
            self.invoke(["//Tests:One"])
        self.assertEqual(run.call_count, 1)
        self.session.create.assert_not_called()
        self.session.boot.assert_not_called()
        self.session.cleanup.assert_called_once()
        self.assertEqual(self.report()["targets"][0]["test"], "not_run")

    def test_test_failure_and_cleanup_failure_are_both_retained(self):
        self.session.cleanup.side_effect = ci.package.PackageError("owned shutdown/delete failed")
        with mock.patch.object(ci.package, "run", side_effect=[None, ci.package.PackageError("native assertion failed")]):
            with self.assertRaises(ci.package.PackageError) as caught:
                self.invoke(["//Tests:One"])
        self.assertIn("native assertion failed", str(caught.exception))
        self.assertIn("owned shutdown/delete failed", str(caught.exception))
        self.session.reset_after_failure.assert_not_called()
        self.session.cleanup.assert_called_once()
        self.assertEqual(self.report()["targets"][0]["test"], "failed")

    def test_failed_recovery_stops_following_tests_and_still_cleans_up(self):
        self.session.reset_after_failure.side_effect = ci.package.PackageError("reboot failed")
        with mock.patch.object(ci.package, "run", side_effect=[None, None, ci.package.PackageError("native crash")]) as run:
            with self.assertRaises(ci.package.PackageError) as caught:
                self.invoke(["//Tests:One", "//Tests:Two"])
        self.assertEqual(run.call_count, 3)
        self.assertIn("native crash", str(caught.exception))
        self.assertIn("reboot failed", str(caught.exception))
        self.assertEqual([item["test"] for item in self.report()["targets"]], ["failed", "not_run"])
        self.session.cleanup.assert_called_once()

    def test_success_still_requires_owned_device_cleanup(self):
        with mock.patch.object(ci.package, "run"):
            self.invoke(["//Tests:One", "//Tests:Two"])
        self.session.create.assert_called_once()
        self.session.boot.assert_called_once()
        self.session.reset_after_failure.assert_not_called()
        self.session.cleanup.assert_called_once()
        self.assertEqual([item["test"] for item in self.report()["targets"]], ["passed", "passed"])

    def test_empty_or_invalid_inventory_starts_no_target(self):
        for targets in ([], ["//Tests:Valid", {"target": "//Tests:Bad", "filter": "unsafe argument"}]):
            with self.subTest(targets=targets), mock.patch.object(ci.package, "run") as run:
                with self.assertRaises(ci.package.PackageError):
                    self.invoke(targets)
                run.assert_not_called()
        self.factory.assert_not_called()

    def test_missing_pinned_simulator_starts_no_build_or_device(self):
        with mock.patch.object(ci.package, "run") as run:
            with self.assertRaisesRegex(ci.package.PackageError, "native_test_simulator"):
                ci.package.run_native_tests(Path("checkout"), Path("config"), Path("cache"), ["//Tests:One"],
                                            artifacts=self.artifacts)
        run.assert_not_called()
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
        self.assertEqual(report["targets"][0]["build"], "passed")
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
        base = {"commit": "b" * 40, "versions": {"macos": "27", "xcode": "27.1"}}
        manifest = {"feature": {"patch": {"sha256": "c" * 64}},
                    "demo": {"available": with_demo, "patch": {"sha256": "d" * 64}}}
        visited = []

        def start(command, **kwargs):
            self.assertNotIn("DRAFT_POCKET_BUILD_CONFIGURATION_JSON", os.environ)
            name = "capture" if Path(command[1]).name == "capture-demo.py" else command[2]
            if name == "test":
                name = "native-tests" if "--native" in command else "portable-tests"
            visited.append(name)
            # Before configuration parsing only the raw secret is in the redaction list.
            # Children never receive it; this synthetic output verifies that early logs still
            # redact an accidental complete-secret echo without requiring parsed credentials.
            echoed_secret = secret_json if name == "portable-tests" else config["api_hash"]
            return mock.Mock(stdout=iter([f"{name} synthetic output {echoed_secret}\n"]),
                             wait=mock.Mock(return_value=results.get(name, 0)))

        def tool(command, **kwargs):
            if command[0] == "sw_vers":
                return b"27.0.1\n"
            if command[0] == "xcodebuild":
                return b"Xcode 27.1\nBuild version synthetic\n"
            if command[:4] == ["xcrun", "simctl", "list", "runtimes"]:
                return json.dumps({"runtimes": [{"name": "iOS 27.0", "identifier": "synthetic-runtime", "isAvailable": True}]}).encode()
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
        self.assertEqual(visited, ["portable-tests", "bootstrap", "check", "build", "capture", "native-tests"])
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
        self.assertEqual(visited[-1], "native-tests")

    def test_capture_failure_still_runs_native_tests(self):
        code, status, artifacts, visited = self.attempt({"capture": 1})
        self.assertEqual(code, 1)
        self.assertEqual(visited, ["portable-tests", "bootstrap", "check", "build", "capture", "native-tests"])
        self.assertEqual(status["stages"]["capture"], "failed")
        self.assertEqual(status["stages"]["native_tests"], "passed")
        self.assertEqual(set(status["failures"]), {"capture"})
        self.assertTrue((artifacts / "capture.log").is_file())
        self.assertTrue((artifacts / "native-tests.log").is_file())

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
        self.assertEqual(visited, ["portable-tests", "bootstrap", "check", "build", "capture", "native-tests"])

    def test_missing_demo_is_preserved_alongside_native_failure(self):
        code, status, _, visited = self.attempt({"native-tests": 1}, with_demo=False)
        self.assertEqual(code, 1)
        self.assertEqual(set(status["failures"]), {"native_tests", "capture"})
        self.assertNotIn("capture", visited)
        self.assertIn("no isolated full-client", status["failures"]["capture"])


if __name__ == "__main__":
    unittest.main()
