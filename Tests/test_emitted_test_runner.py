"""Exercise declared identity, fresh outputs and real owned-script supervision."""

import ctypes
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import emitted_test_runner as emitted


TARGET = "//Tests/DraftPocketDemo:DraftPocketDemoUITests"
TARGET_PATH = "Tests/DraftPocketDemo/DraftPocketDemoUITests"
FILTER = "DraftPocketDemoUITests/testCaptureProductionStory"
UDID = "CA1C775E-9033-4666-9B09-1E195CA035B5"


class EmittedIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "fixture space"
        self.root.mkdir()
        self.checkout = self.root / "workspace"
        self.execroot = self.root / "output-base/execroot/_main"
        self.output = self.execroot / "bazel-out"
        self.bin = self.output / "darwin_arm64-dbg/bin"
        self.runner = self.bin / TARGET_PATH
        self.runner.parent.mkdir(parents=True)
        self.runner.write_text("#!/bin/bash\necho runner\n", encoding="utf-8")
        self.runfiles = Path(str(self.runner) + ".runfiles")
        (self.runfiles / "_main").mkdir(parents=True)
        self.checkout.mkdir()
        def alias(path, physical):
            if os.name == "nt":
                subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "New-Item -ItemType Junction -Path $env:DRAFT_EMIT_ALIAS -Target $env:DRAFT_EMIT_PHYSICAL | Out-Null"],
                    env={**os.environ, "DRAFT_EMIT_ALIAS": str(path), "DRAFT_EMIT_PHYSICAL": str(physical)},
                    check=True, capture_output=True)
            else:
                path.symlink_to(physical, target_is_directory=True)
        alias(self.checkout / "bazel-out", self.output)
        alias(self.checkout / "bazel-bin", self.bin)
        alias(self.runfiles / "_main/Tests", self.bin / "Tests")
        self.manifest = Path(str(self.runner) + ".runfiles_manifest")
        self.manifest.write_text("_main/" + TARGET_PATH + " " + str(self.runner) + "\n", encoding="utf-8")
        self.setup = self.execroot / emitted.SETUP
        self.setup.parent.mkdir(parents=True)
        self.setup.write_text("#!/bin/bash\n# fixture setup\n", encoding="utf-8")
        self.pin = mock.patch.object(emitted, "SETUP_SHA256", hashlib.sha256(self.setup.read_bytes()).hexdigest())
        self.pin.start()
        self.addCleanup(self.pin.stop)
        private = self.root / "private"
        private.mkdir(mode=0o700)
        self.script = private / "story.sh"
        testdir = "bazel-out/darwin_arm64-dbg/testlogs/" + TARGET_PATH
        runfiles = "bazel-out/darwin_arm64-dbg/bin/" + TARGET_PATH + ".runfiles"
        self.env = {"BUILD_EXECROOT": str(self.execroot), "BUILD_WORKSPACE_DIRECTORY": str(self.checkout),
                    "BUILD_WORKING_DIRECTORY": str(self.checkout), "TEST_WORKSPACE": "_main",
                    "TEST_TARGET": TARGET, "TEST_BINARY": TARGET_PATH, "TESTBRIDGE_TEST_ONLY": FILTER,
                    "TEST_UNDECLARED_OUTPUTS_DIR": testdir + "/test.outputs",
                    "XML_OUTPUT_FILE": testdir + "/test.xml", "TEST_TMPDIR": "_tmp/" + "a" * 32,
                    **dict.fromkeys(("TEST_SRCDIR", "JAVA_RUNFILES", "PYTHON_RUNFILES", "RUNFILES_DIR"), runfiles)}
        self.arguments = [emitted.SETUP, TARGET_PATH, "--destination=platform=ios_simulator,id=" + UDID]
        self.write()

    def write(self):
        self.script.write_bytes(emitted._format("/bin/bash", str(self.execroot), self.env, self.arguments).encode("latin-1"))
        self.script.chmod(0o700)

    def validate(self):
        return emitted.validate_emitted_script(self.checkout, self.script, target=TARGET, test_filter=FILTER, udid=UDID)

    def test_exact_main_repository_identity_and_fresh_output_are_exposed_without_environment(self):
        self.env["OWNER_SECRET"] = "private value not for evidence"
        self.write()
        plan = self.validate()
        self.assertEqual(plan.output_directory, self.output / "darwin_arm64-dbg/testlogs" / TARGET_PATH / "test.outputs")
        self.assertEqual(plan.test_directory, plan.output_directory.parent)
        self.assertEqual(plan.identity["test_filter"], FILTER)
        self.assertNotIn("private value", json.dumps(plan.identity))
        self.assertNotIn("OWNER_SECRET", json.dumps(plan.identity))

    def test_unfiltered_native_target_requires_filter_environment_to_be_absent(self):
        self.env.pop("TESTBRIDGE_TEST_ONLY")
        self.write()
        plan = emitted.validate_emitted_script(self.checkout, self.script, target=TARGET, test_filter=None, udid=UDID)
        self.assertIsNone(plan.test_filter)
        self.env["TESTBRIDGE_TEST_ONLY"] = ""
        self.write()
        with self.assertRaises(emitted.package.PackageError):
            emitted.validate_emitted_script(self.checkout, self.script, target=TARGET, test_filter=None, udid=UDID)

    def test_prior_take_output_and_later_output_reappearance_are_rejected(self):
        plan = self.validate()
        plan.output_directory.mkdir(parents=True)
        (plan.output_directory / "test.xcresult").mkdir()
        with self.assertRaisesRegex(emitted.package.PackageError, "prior take"):
            self.validate()
        if os.name == "posix":
            artifacts = self.root / "evidence"
            artifacts.mkdir()
            with mock.patch.object(emitted, "DiskGuard") as guard:
                # Validation occurs before any process or disk watcher is started.
                with self.assertRaises(emitted.package.PackageError):
                    emitted.run_emitted(plan, artifacts, stage="story", timeout_seconds=1, on_line=lambda _: None)
                guard.assert_not_called()

    def test_target_filter_uuid_and_output_escape_fail_closed(self):
        for key, value in (("TEST_TARGET", "//Tests/Other:Other"), ("TESTBRIDGE_TEST_ONLY", "Other/test"),
                           ("TEST_UNDECLARED_OUTPUTS_DIR", "../outside"),
                           ("XML_OUTPUT_FILE", "bazel-out/darwin_arm64-dbg/testlogs/Other/test.xml"),
                           ("TEST_TMPDIR", "/tmp/arbitrary"), ("BASH_ENV", "/tmp/hook")):
            original = dict(self.env)
            self.env[key] = value
            self.write()
            with self.subTest(key=key), self.assertRaises(emitted.package.PackageError):
                self.validate()
            self.env = original
        self.arguments[-1] = self.arguments[-1].lower()
        self.write()
        with self.assertRaisesRegex(emitted.package.PackageError, "destination"):
            self.validate()

    def test_shell_injection_noncanonical_syntax_and_appended_commands_are_not_evaluated(self):
        original = self.script.read_bytes()
        for data in (original + b"\necho injected", original.replace(b"  exec env", b"  exec  env"),
                     original.replace(b"#!/bin/bash", b"#!/usr/bin/env bash"),
                     original.replace(b" && ", b"; echo bad; ")):
            self.script.write_bytes(data)
            with self.subTest(data=data[:20]), self.assertRaises(emitted.package.PackageError):
                self.validate()

    def test_pinned_wrapper_and_runner_manifest_cannot_point_at_another_build(self):
        original = self.setup.read_bytes()
        self.setup.write_bytes(original + b"echo changed\n")
        with self.assertRaisesRegex(emitted.package.PackageError, "pinned Bazel"):
            self.validate()
        self.setup.write_bytes(original)
        self.manifest.write_text("_main/" + TARGET_PATH + " " + str(self.setup) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(emitted.package.PackageError, "uniquely match"):
            self.validate()

    def test_escaped_runfiles_space_and_quote_in_private_path_preserve_exact_identity(self):
        moved = self.root / "private space's"
        self.script.parent.rename(moved)
        self.script = moved / self.script.name
        # Bazel manifest escaping, including a space, is independently decoded.
        escaped = lambda value: value.replace("\\", "\\b").replace(" ", "\\s")
        self.manifest.write_text(" " + escaped("_main/" + TARGET_PATH) + " " + escaped(str(self.runner)) + "\n", encoding="utf-8")
        self.assertEqual(self.validate().script_path, self.script)


@unittest.skipUnless(os.name == "posix", "Real emitted-script execution requires POSIX")
class EmittedExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.artifacts = self.root / "evidence"
        self.artifacts.mkdir()
        private = self.root / "private"
        private.mkdir(mode=0o700)
        self.script = private / "test.sh"
        self.plan = emitted.EmittedPlan(self.root, self.script, self.root / "native", self.root,
                                       TARGET, FILTER, UDID, {"script_sha256": "fixture"})
        self.validation = mock.patch.object(emitted, "validate_emitted_script", return_value=self.plan)
        self.validator_mock = self.validation.start()
        self.addCleanup(self.validation.stop)
        original_guard = emitted.DiskGuard
        self.guard_instances = []
        def guard(artifacts, stage):
            result = original_guard(artifacts, stage, minimum_free_bytes=1, interval_seconds=0.05,
                                    interrupt_timeout=0.3, terminate_timeout=0.3, kill_timeout=1)
            self.guard_instances.append(result)
            return result
        self.guard_factory = mock.patch.object(emitted, "DiskGuard", side_effect=guard)
        self.guard_factory.start()
        self.addCleanup(self.guard_factory.stop)

    def program(self, source):
        self.script.write_text("#!" + sys.executable + "\n" + source, encoding="utf-8")
        self.script.chmod(0o700)

    def run_script(self, callback=None, timeout=3):
        return emitted.run_emitted(self.plan, self.artifacts, stage="story", timeout_seconds=timeout,
                                   on_line=callback or (lambda _: None))

    def test_real_exit_code_and_lines_are_preserved_without_claiming_native_success(self):
        self.program("import sys\nprint('DRAFT_POCKET_EVENT fixture', flush=True)\nsys.exit(7)\n")
        lines = []
        result = self.run_script(lines.append)
        self.assertEqual(result.returncode, 7)
        self.assertFalse(result.timed_out)
        self.assertEqual(result.supervision_errors, [])
        self.assertEqual(lines, ["DRAFT_POCKET_EVENT fixture\n"])
        self.assertEqual(json.loads((self.artifacts / "emitted-story.json").read_text())["returncode"], 7)
        self.assertIsNotNone(self.guard_instances[0]._pgid)
        with self.assertRaisesRegex(emitted.package.PackageError, "already been used"):
            self.run_script(lines.append)

    def test_stdout_eof_before_successful_process_exit_is_not_an_abort(self):
        self.program("import os,time\nos.close(1)\nos.close(2)\ntime.sleep(.15)\n")
        result = self.run_script()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.supervision_errors, [])

    def test_silent_command_deadline_stops_owned_group(self):
        self.program("import time\ntime.sleep(30)\n")
        result = self.run_script(timeout=0.2)
        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.guard_instances[0]._group_absent)

    def test_callback_failure_and_keyboard_interrupt_still_drain_owned_group(self):
        self.program("import time\nprint('ready', flush=True)\ntime.sleep(30)\n")
        def failure(_):
            raise OSError("do not disclose arbitrary message")
        result = self.run_script(failure)
        self.assertTrue(any("OSError" in item for item in result.supervision_errors))
        self.assertNotIn("do not disclose", json.dumps(result.supervision_errors))
        self.assertTrue(self.guard_instances[-1]._group_absent)
        self.script = self.script.with_name("interrupt.sh")
        self.plan = emitted.EmittedPlan(self.root, self.script, self.root / "native", self.root,
                                       TARGET, FILTER, UDID, {"script_sha256": "fixture"})
        self.validator_mock.return_value = self.plan
        self.program("import time\nprint('ready', flush=True)\ntime.sleep(30)\n")
        def interrupt(_):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_script(interrupt)
        self.assertTrue(self.guard_instances[-1]._group_absent)

    def test_rejected_ownership_never_releases_runner(self):
        marker = self.root / "must-not-start"
        self.program("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('started')\n")
        with mock.patch("disk_guard.os.getpgid", return_value=os.getpgrp()):
            result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())
        self.assertTrue(result.supervision_errors)

    def test_sigterm_cancellation_drains_group_and_restores_original_handler(self):
        previous = signal.getsignal(signal.SIGTERM)
        self.program("import time\nprint('ready', flush=True)\ntime.sleep(30)\n")
        def cancel(_):
            os.kill(os.getpid(), signal.SIGTERM)
        with self.assertRaises(SystemExit) as result:
            self.run_script(cancel)
        self.assertEqual(result.exception.code, 128 + signal.SIGTERM)
        self.assertTrue(self.guard_instances[-1]._group_absent)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    @unittest.skipUnless(sys.platform == "linux", "Linux verifies orphan reaping with a temporary subreaper")
    def test_fast_leader_exit_stubborn_descendant_and_unrelated_session(self):
        libc = ctypes.CDLL(None, use_errno=True)
        old = ctypes.c_int()
        self.assertEqual(libc.prctl(37, ctypes.byref(old), 0, 0, 0), 0)
        pid_path = self.root / "child.pid"
        child = ("import os,signal,time;from pathlib import Path;"
                 "signal.signal(signal.SIGINT,signal.SIG_IGN);signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                 "Path(" + repr(str(pid_path)) + ").write_text(str(os.getpid()));time.sleep(30)")
        self.program("import subprocess,sys,time\nfrom pathlib import Path\n"
                     "subprocess.Popen([sys.executable,'-c'," + repr(child) + "])\n"
                     "while not Path(" + repr(str(pid_path)) + ").exists(): time.sleep(.001)\n")
        sentinel = None
        stopped, reaped = threading.Event(), threading.Event()
        descendant = []
        def reap():
            while not stopped.wait(0.01):
                if not descendant:
                    value = pid_path.read_text() if pid_path.exists() else ""
                    if value.isdigit():
                        descendant.append(int(value))
                if descendant:
                    try:
                        found, _ = os.waitpid(descendant[0], os.WNOHANG)
                        if found:
                            reaped.set()
                            return
                    except ChildProcessError:
                        pass
        reaper = threading.Thread(target=reap, daemon=True)
        self.assertEqual(libc.prctl(36, 1, 0, 0, 0), 0)
        try:
            sentinel = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], start_new_session=True,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            reaper.start()
            result = self.run_script(timeout=3)
            self.assertEqual(result.returncode, 0)
            self.assertTrue(any("outlived" in error for error in result.supervision_errors))
            self.assertTrue(reaped.wait(1))
            self.assertTrue(self.guard_instances[-1]._group_absent)
            self.assertIsNone(sentinel.poll())
        finally:
            try:
                for guard in self.guard_instances:
                    guard.stop(terminate=True)
                if descendant and not reaped.is_set():
                    try:
                        if os.getpgid(descendant[0]) == self.guard_instances[-1]._pgid:
                            os.kill(descendant[0], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    reaped.wait(1)
            finally:
                stopped.set()
                if reaper.ident is not None:
                    reaper.join(1)
                try:
                    if sentinel is not None:
                        sentinel.terminate()
                        try:
                            sentinel.wait(2)
                        except subprocess.TimeoutExpired:
                            sentinel.kill()
                            sentinel.wait(2)
                finally:
                    self.assertEqual(libc.prctl(36, old.value, 0, 0, 0), 0)


if __name__ == "__main__":
    unittest.main()
