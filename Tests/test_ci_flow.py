"""Independent native-test and capture failures must not suppress each other."""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("ci_simulator", ROOT / "scripts/ci-simulator.py")
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


class NativeTargetAggregationTests(unittest.TestCase):
    def test_every_target_runs_and_all_failures_are_reported(self):
        targets = ["//Tests:One", "//Tests:Two", {"target": "//Tests:Three", "filter": "SelectedTests"},
                   "//Tests:Four", "//Tests:Five"]
        visited = []

        def execute(command, **kwargs):
            target = command[command.index("--target") + 1]
            visited.append(target)
            if target == "//Tests:Two":
                raise ci.package.PackageError("compiler exited 1")
            if target == "//Tests:Four":
                raise OSError("runner failed to start")

        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(ci.package, "run", side_effect=execute) as run, redirect_stdout(output), redirect_stderr(errors):
            with self.assertRaises(ci.package.PackageError) as caught:
                ci.package.run_native_tests(Path("checkout"), Path("config"), Path("cache"), targets)
        self.assertEqual(visited, ["//Tests:" + name for name in ("One", "Two", "Three", "Four", "Five")])
        self.assertIn("2 of 5", str(caught.exception))
        self.assertIn("//Tests:Two: compiler exited 1", str(caught.exception))
        self.assertIn("//Tests:Four: runner failed to start", str(caught.exception))
        self.assertIn("Native test target PASSED: //Tests:Five", output.getvalue())
        self.assertEqual(run.call_args_list[2].args[0][-2:], ["--testFilter", "SelectedTests"])

    def test_empty_or_invalid_inventory_starts_no_target(self):
        for targets in ([], ["//Tests:Valid", {"target": "//Tests:Bad", "filter": "unsafe argument"}]):
            with self.subTest(targets=targets), mock.patch.object(ci.package, "run") as run:
                with self.assertRaises(ci.package.PackageError):
                    ci.package.run_native_tests(Path("checkout"), Path("config"), Path("cache"), targets)
                run.assert_not_called()


class SimulatorFlowTests(unittest.TestCase):
    def attempt(self, results, with_demo=True):
        """Exercise main's real status/log/timing writes with synthetic processes."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        artifacts = root / "evidence"
        config = json.loads((ROOT / "Integration/build-configuration.example.json").read_text())
        config.update(api_id="123456", api_hash="a" * 32)
        base = {"commit": "b" * 40, "versions": {"macos": "27", "xcode": "27.1"}}
        manifest = {"feature": {"patch": {"sha256": "c" * 64}},
                    "demo": {"available": with_demo, "patch": {"sha256": "d" * 64}}}
        visited = []

        def start(command, **kwargs):
            self.assertNotIn("DRAFT_POCKET_BUILD_CONFIGURATION_JSON", os.environ)
            name = "capture" if Path(command[1]).name == "capture-demo.py" else command[2]
            visited.append(name)
            return mock.Mock(stdout=iter([f"{name} synthetic output {config['api_hash']}\n"]),
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
                "DRAFT_POCKET_BUILD_CONFIGURATION_JSON": json.dumps(config),
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

    def test_native_failure_still_attempts_capture_and_exit_stays_failed(self):
        code, status, artifacts, visited = self.attempt({"test": 1})
        self.assertEqual(code, 1)
        self.assertEqual(visited, ["bootstrap", "check", "build", "test", "capture"])
        self.assertEqual(status["stages"]["build"], "passed")
        self.assertEqual(status["stages"]["native_tests"], "failed")
        self.assertEqual(status["stages"]["capture"], "passed")
        self.assertEqual(set(status["failures"]), {"native_tests"})
        self.assertEqual(set(status["stage_times_utc"]["native_tests"]), {"running", "failed"})
        self.assertEqual(set(status["stage_times_utc"]["capture"]), {"running", "passed"})
        self.assertTrue((artifacts / "native-tests.log").is_file())
        self.assertTrue((artifacts / "capture.log").is_file())

    def test_both_failures_and_their_timestamps_survive(self):
        code, status, artifacts, visited = self.attempt({"test": 1, "capture": 2})
        self.assertEqual(code, 1)
        self.assertEqual(set(status["failures"]), {"native_tests", "capture"})
        for stage in ("native_tests", "capture"):
            self.assertEqual(status["stages"][stage], "failed")
            self.assertEqual(set(status["stage_times_utc"][stage]), {"running", "failed"})
            self.assertIn(stage + ":", (artifacts / "failure.txt").read_text())
            self.assertTrue((artifacts / (stage + "-failure.txt")).is_file())
        self.assertEqual(visited[-1], "capture")

    def test_failed_app_build_prevents_tests_and_capture(self):
        code, status, _, visited = self.attempt({"build": 1})
        self.assertEqual(code, 1)
        self.assertEqual(visited, ["bootstrap", "check", "build"])
        self.assertEqual(status["stages"]["native_tests"], "not_run")
        self.assertEqual(status["stages"]["capture"], "not_run")
        self.assertEqual(set(status["failures"]), {"build"})

    def test_all_success_has_no_failure_artifact(self):
        code, status, artifacts, visited = self.attempt({})
        self.assertEqual(code, 0)
        self.assertTrue(all(value == "passed" for value in status["stages"].values()))
        self.assertNotIn("failures", status)
        self.assertFalse((artifacts / "failure.txt").exists())
        self.assertEqual(visited[-1], "capture")

    def test_missing_demo_is_preserved_alongside_native_failure(self):
        code, status, _, visited = self.attempt({"test": 1}, with_demo=False)
        self.assertEqual(code, 1)
        self.assertEqual(set(status["failures"]), {"native_tests", "capture"})
        self.assertNotIn("capture", visited)
        self.assertIn("no isolated full-client", status["failures"]["capture"])


if __name__ == "__main__":
    unittest.main()
