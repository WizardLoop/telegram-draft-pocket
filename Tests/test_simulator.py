"""Owned device lifecycle, startup evidence, and framebuffer prerequisites."""

import json
import hashlib
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import package
from simulator import SimulatorSession


class SimulatorSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(self.temporary.cleanup)
        self.session = SimulatorSession(self.root / "evidence")
        self.udid = "CA1C775E-9033-4666-9B09-1E195CA035B5"
        self.other = "FA1C775E-9033-4666-9B09-1E195CA035B5"
        self.state = "Shutdown"
        self.policy = None
        self.commands = []
        self.run = mock.patch("simulator.subprocess.run", side_effect=self.command).start()
        self.addCleanup(mock.patch.stopall)

    def command(self, command, **kwargs):
        self.commands.append(command)
        self.assertGreater(kwargs["timeout"], 0)
        self.assertLessEqual(kwargs["timeout"], 180)
        value, code = b"", 0
        if command[0] == "/usr/sbin/sysctl":
            value = b"hw.memsize: 15000000000\nvm.swapusage: total = 2M\n"
        elif command[0] == "/usr/bin/vm_stat":
            value = b"Pages free: 123\n"
        elif command[0] == "/bin/ps":
            self.assertEqual(command[-1], "pid=,ppid=,rss=,comm=")
            value = (b"123 1 123 /runtime/launchd_sim\n456 1 456 java\n"
                     b"457 1 456 bazel(checkout)\n458 1 456 bazel-9.2.0-dar\n"
                     b"459 1 456 /build/swift_worker\n460 1 456 /Xcode/com.apple.CoreSimulator.CoreSimulatorService\n"
                     b"789 1 789 /private/unrelated-app\n")
        elif command[:4] == ["xcrun", "simctl", "list", "runtimes"]:
            value = json.dumps({"runtimes": [{"name": "iOS 27.0", "version": "27.0", "isAvailable": True,
                                             "identifier": "com.apple.CoreSimulator.SimRuntime.iOS-27-0"}]}).encode()
        elif command[:4] == ["xcrun", "simctl", "list", "devicetypes"]:
            value = b'{"devicetypes":[{"name":"iPhone 17","identifier":"com.apple.CoreSimulator.SimDeviceType.iPhone-17"}]}'
        elif command[:4] == ["xcrun", "simctl", "list", "devices"]:
            devices = [{"udid": self.other, "state": "Booted", "name": "private unrelated device"}]
            if self.state is not None:
                devices.append({"udid": self.udid, "state": self.state, "name": "owned"})
            value = json.dumps({"devices": {"runtime": devices}}).encode()
        elif command[:3] == ["xcrun", "simctl", "create"]:
            value = (self.udid + "\n").encode()
        elif command[:3] == ["xcrun", "simctl", "boot"]:
            self.assertEqual(command[3], self.udid)
            self.state = "Booted"
        elif command[:3] == ["xcrun", "simctl", "shutdown"]:
            self.assertEqual(command[3], self.udid)
            self.state = "Shutdown"
        elif command[:3] == ["xcrun", "simctl", "delete"]:
            self.assertEqual(command[3], self.udid)
            self.state = None
        elif command[:3] == ["xcrun", "simctl", "io"]:
            Path(command[-1]).write_bytes(b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
                                        + (1206).to_bytes(4, "big") + (2622).to_bytes(4, "big"))
        elif command[0] == "ffmpeg":
            value = bytes(range(256))
        elif command[:2] == ["defaults", "read"]:
            value, code = ((b"none\n", 0) if self.policy == "none" else (b"does not exist\n", 1))
        elif command[:2] == ["defaults", "delete"]:
            self.policy = None
        elif command[:2] == ["defaults", "write"]:
            self.policy = "none"
        return subprocess.CompletedProcess(command, code, stdout=value)

    def test_lifecycle_mutates_only_created_uuid_and_health_omits_unrelated_names(self):
        self.session.create()
        self.session.boot()
        self.session.cleanup()
        self.assertIsNone(self.state)
        report = (self.session.artifacts / "simulator-lifecycle.json").read_text()
        self.assertIn('"Booted": 2', report)
        self.assertIn("launchd_sim", report)
        self.assertIn("bazel(checkout)", report)
        self.assertIn("bazel-9.2.0-dar", report)
        self.assertIn("swift_worker", report)
        self.assertIn("CoreSimulatorService", report)
        self.assertNotIn(self.other, report)
        self.assertNotIn("private unrelated", report)
        self.assertNotIn("unrelated-app", report)
        mutations = [command for command in self.commands if len(command) > 2 and command[2] in ("boot", "shutdown", "delete")]
        self.assertTrue(mutations)
        self.assertTrue(all(command[3] == self.udid for command in mutations))

    def test_unavailable_exact_runtime_never_creates_a_substitute(self):
        self.session.os_version = "27.2"
        with self.assertRaisesRegex(package.PackageError, "exact Simulator"):
            self.session.create()
        self.assertFalse(any(command[:3] == ["xcrun", "simctl", "create"] for command in self.commands))

    def test_invalid_identity_data_is_a_package_error(self):
        def command(args, **kwargs):
            if args[:3] == ["xcrun", "simctl", "create"]:
                return subprocess.CompletedProcess(args, 0, stdout=b"not-a-uuid")
            return self.command(args, **kwargs)
        self.run.side_effect = command
        with self.assertRaisesRegex(package.PackageError, "invalid identity"):
            self.session.create()
        self.assertIsNone(self.session.udid)

    def test_boot_timeout_keeps_partial_log_and_can_still_cleanup(self):
        self.session.create()
        def command(args, **kwargs):
            if args[:3] == ["xcrun", "simctl", "bootstatus"]:
                raise subprocess.TimeoutExpired(args, kwargs["timeout"], output=b"Waiting for SpringBoard\n")
            return self.command(args, **kwargs)
        self.run.side_effect = command
        with self.assertRaisesRegex(package.PackageError, "bootstatus exceeded 180s"):
            self.session.boot()
        phase = next(item for item in self.session.evidence["phases"] if item["name"] == "bootstatus")
        self.assertEqual(phase["status"], "timed_out")
        self.assertEqual((self.session.artifacts / phase["log"]).read_bytes(), b"Waiting for SpringBoard\n")
        self.session.cleanup()
        self.assertIsNone(self.state)

    def test_reset_of_already_shutdown_owned_device_still_boots(self):
        self.session.create()
        self.session.reset_after_failure()
        self.assertEqual(self.state, "Booted")
        self.assertFalse(any(command[:3] == ["xcrun", "simctl", "shutdown"] for command in self.commands))
        self.session.cleanup()

    def test_cleanup_delete_is_attempted_even_if_shutdown_fails(self):
        self.session.create()
        self.state = "Booted"
        def command(args, **kwargs):
            if args[:3] == ["xcrun", "simctl", "shutdown"]:
                return subprocess.CompletedProcess(args, 1, stdout=b"shutdown failure")
            return self.command(args, **kwargs)
        self.run.side_effect = command
        with self.assertRaisesRegex(package.PackageError, "cleanup failed"):
            self.session.cleanup()
        self.assertIsNone(self.state)

    def test_cleanup_and_renderer_restore_survive_health_and_evidence_io_failure(self):
        self.session.create()
        self.state = "Booted"
        self.session._restore_renderer = True
        with mock.patch.object(self.session, "health", side_effect=OSError("health disk full")), \
                mock.patch.object(self.session, "_save", side_effect=OSError("lifecycle disk full")), \
                mock.patch.object(Path, "write_bytes", side_effect=OSError("log disk full")), \
                self.assertRaisesRegex(package.PackageError, "cleanup failed") as failure:
            self.session.cleanup()
        self.assertIsNone(self.state)
        self.assertEqual(self.policy, "none")
        self.assertFalse(self.session._restore_renderer)
        self.assertIn("health disk full", str(failure.exception))
        self.assertIn("lifecycle disk full", str(failure.exception))
        self.assertIn("log disk full", str(failure.exception))
        self.assertTrue(any(command[:3] == ["xcrun", "simctl", "shutdown"] for command in self.commands))
        self.assertTrue(any(command[:3] == ["xcrun", "simctl", "delete"] for command in self.commands))

    def test_disabled_renderer_on_local_host_is_not_changed(self):
        self.session.create()
        self.policy = "none"
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), self.assertRaisesRegex(package.PackageError, "no local host preference"):
            self.session.prepare_framebuffer()
        self.assertEqual(self.policy, "none")
        self.assertFalse(any(command[:2] == ["defaults", "delete"] for command in self.commands))
        self.session.cleanup()

    def test_hosted_renderer_override_is_scoped_and_restored_during_cleanup(self):
        self.session.create()
        self.policy = "none"
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_TEMP": str(self.root)}):
            self.session.prepare_framebuffer()
        self.assertIsNone(self.policy)
        self.session.cleanup()
        self.assertEqual(self.policy, "none")

    def test_framebuffer_requires_decoded_nonblank_pixels(self):
        self.session.create()
        self.session.probe_framebuffer()
        self.assertEqual(self.session.evidence["framebuffer"]["decoded_gray_range"], 255)
        def command(args, **kwargs):
            if args[0] == "ffmpeg":
                return subprocess.CompletedProcess(args, 0, stdout=bytes(256))
            return self.command(args, **kwargs)
        self.run.side_effect = command
        with self.assertRaisesRegex(package.PackageError, "undecodable or blank"):
            self.session.probe_framebuffer()
        self.session.cleanup()


class HostedBuildServerTests(unittest.TestCase):
    def create_alias(self, alias, physical):
        if os.name == "nt":
            self.unmocked_run([self.powershell, "-NoProfile", "-NonInteractive", "-Command",
                "New-Item -ItemType Junction -Path $env:DRAFT_TEST_ALIAS -Target $env:DRAFT_TEST_PHYSICAL | Out-Null"],
                env={**self.unmocked_environment, "DRAFT_TEST_ALIAS": str(alias), "DRAFT_TEST_PHYSICAL": str(physical)},
                check=True, capture_output=True)
        else:
            alias.symlink_to(physical, target_is_directory=True)

    def setUp(self):
        self.unmocked_run = subprocess.run
        self.unmocked_environment = dict(os.environ)
        self.powershell = shutil.which("powershell") if os.name == "nt" else None
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        self.base = self.root / "bazel-output-base"
        self.outputs = self.base / "execroot/_main/bazel-out"
        self.outputs.mkdir(parents=True)
        self.alias = self.checkout / "bazel-out"
        self.create_alias(self.alias, self.outputs)
        self.binary = self.checkout / "build-input/bazel-9.2.0-darwin-arm64"
        self.binary.parent.mkdir()
        self.binary.write_bytes(b"synthetic pinned Bazel executable; never run")
        self.pin = "9.2.0:" + hashlib.sha256(self.binary.read_bytes()).hexdigest()
        (self.checkout / "versions.json").write_text(json.dumps({"bazel": self.pin}))
        self.package_root = self.root / "package"
        (self.package_root / "Integration").mkdir(parents=True)
        (self.package_root / "Integration/BASE.json").write_text(json.dumps({"versions": {"bazel": self.pin}}))
        self.output_file = self.outputs / "preserved-output"
        self.output_file.write_bytes(b"compiled artifact")
        self.session = SimulatorSession(self.root / "evidence")
        self.calls = []
        self.workspace = self.checkout
        self.reported_base = self.base
        self.info_code = self.shutdown_code = 0
        self.shutdown_timeout = False
        self.environment = mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true",
            "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_TEMP": str(self.root)}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.root_patch = mock.patch.object(package, "ROOT", self.package_root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.run = mock.patch("simulator.subprocess.run", side_effect=self.command)
        self.run.start()
        self.addCleanup(self.run.stop)
        self.health = mock.patch.object(self.session, "health", side_effect=lambda name: self.calls.append(name))
        self.health.start()
        self.addCleanup(self.health.stop)

    def command(self, command, **kwargs):
        self.calls.append(command)
        self.assertEqual(Path(command[0]), self.binary)
        self.assertEqual(kwargs["cwd"], self.checkout)
        self.assertIn("--noblock_for_lock", command)
        self.assertLessEqual(kwargs["timeout"], 60)
        if "info" in command:
            self.assertEqual(command[-2:], ["workspace", "output_base"])
            output = f"workspace: {self.workspace}\noutput_base: {self.reported_base}\n".encode()
            return subprocess.CompletedProcess(command, self.info_code, stdout=output)
        self.assertEqual(command[-1], "shutdown")
        self.assertIn("--output_base=" + str(self.base), command)
        if self.shutdown_timeout:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"], output=b"partial shutdown\n")
        return subprocess.CompletedProcess(command, self.shutdown_code, stdout=b"shutdown result\n")

    def test_verified_server_shutdown_retains_artifacts_and_measures_before_after(self):
        self.session.release_build_server(self.checkout)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.calls[0][-3:], ["info", "workspace", "output_base"])
        self.assertEqual(self.calls[1], "before-build-server-release")
        self.assertEqual(self.calls[2][-1], "shutdown")
        self.assertEqual(self.calls[3], "after-build-server-release")
        self.assertEqual(self.output_file.read_bytes(), b"compiled artifact")
        self.assertEqual(self.session.evidence["build_server_release"]["status"], "passed")
        self.assertIsNone(self.session.udid)

    def test_local_or_self_hosted_server_is_never_touched(self):
        for environment in ({"GITHUB_ACTIONS": "false"}, {"RUNNER_ENVIRONMENT": "self-hosted"}):
            with self.subTest(environment=environment), mock.patch.dict(os.environ, environment):
                self.session.release_build_server(self.checkout)
                self.assertEqual(self.session.evidence["build_server_release"]["status"], "skipped")
        self.assertEqual(self.calls, [])

    def test_system_temp_prefix_alias_resolves_to_the_same_workspace_and_output_base(self):
        # Models Darwin /var -> /private/var: only the physical identities are
        # compared, while the caller, RUNNER_TEMP and Bazel may use either prefix.
        with tempfile.TemporaryDirectory() as temporary:
            alias = Path(temporary).resolve() / "var"
            self.create_alias(alias, self.root)
            self.workspace = alias / "checkout"
            self.reported_base = alias / self.base.name
            with mock.patch.dict(os.environ, {"RUNNER_TEMP": str(alias)}):
                self.session.release_build_server(alias / "checkout")
        self.assertEqual(self.session.evidence["build_server_release"]["status"], "passed")
        self.assertEqual(self.calls[2][-2], "--output_base=" + str(self.base))

    def test_wrong_workspace_output_base_and_missing_identity_prevent_shutdown(self):
        other = self.root / "other"
        other.mkdir()
        for workspace, base in ((other, self.base), (self.checkout, other), (self.checkout, self.root / "missing")):
            self.workspace, self.reported_base = workspace, base
            with self.subTest(workspace=workspace, base=base), self.assertRaises(package.PackageError):
                self.session.release_build_server(self.checkout)
        self.assertFalse(any(isinstance(call, list) and call[-1] == "shutdown" for call in self.calls))

    def test_checkout_and_artifacts_must_both_stay_inside_runner_temp(self):
        for path in (self.checkout, self.session.artifacts):
            with self.subTest(temporary=path), mock.patch.dict(os.environ, {"RUNNER_TEMP": str(path)}), \
                    self.assertRaisesRegex(package.PackageError, "inside RUNNER_TEMP"):
                self.session.release_build_server(self.checkout)
        self.assertEqual(self.calls, [])

    def test_changed_binary_is_not_executed(self):
        self.binary.write_bytes(b"untrusted replacement")
        with self.assertRaisesRegex(package.PackageError, "pinned SHA-256"):
            self.session.release_build_server(self.checkout)
        self.assertEqual(self.calls, [])

    def test_busy_server_is_refused_without_shutdown_or_retry(self):
        self.info_code = 9
        with self.assertRaisesRegex(package.PackageError, "bazel-identity exited 9"):
            self.session.release_build_server(self.checkout)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.session.evidence["build_server_release"]["status"], "failed")

    def test_failed_or_timed_out_shutdown_keeps_failure_and_after_health(self):
        for timeout in (False, True):
            self.shutdown_code, self.shutdown_timeout = 9, timeout
            with self.subTest(timeout=timeout), self.assertRaises(package.PackageError):
                self.session.release_build_server(self.checkout)
            self.assertEqual(self.calls[-1], "after-build-server-release")
            self.assertEqual(self.session.evidence["build_server_release"]["status"], "failed")
            self.assertEqual(self.output_file.read_bytes(), b"compiled artifact")


if __name__ == "__main__":
    unittest.main()
