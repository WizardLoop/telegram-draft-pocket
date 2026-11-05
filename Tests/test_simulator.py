"""Owned device lifecycle, startup evidence, and framebuffer prerequisites."""

import json
import os
from pathlib import Path
import subprocess
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
            value = b"123 1 123 /runtime/launchd_sim\n456 1 456 /bin/java\n789 1 789 /private/unrelated-app\n"
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


if __name__ == "__main__":
    unittest.main()
