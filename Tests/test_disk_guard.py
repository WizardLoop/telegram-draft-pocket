"""Storage pressure must stop only the verified command and remain a failure."""

import importlib.util
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


SPEC = importlib.util.spec_from_file_location("disk_guard", Path(__file__).parents[1] / "scripts/disk_guard.py")
disk = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(disk)


class FakeProcess:
    pid = 424242

    def __init__(self):
        self.returncode = None
        self.group_alive = True
        self.waits = []

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        self.waits.append(timeout)
        if self.returncode is None:
            raise subprocess.TimeoutExpired("owned-command", timeout)
        return self.returncode


class DiskGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.process = FakeProcess()
        self.guard = disk.DiskGuard(self.root, "capture", interval_seconds=0.01,
                                   interrupt_timeout=0.02, terminate_timeout=0.02, kill_timeout=0.02)
        self.space = mock.patch.object(disk.shutil, "disk_usage", return_value=SimpleNamespace(free=4 * 1024 ** 3)).start()
        self.group = mock.patch.object(disk.os, "getpgid", return_value=self.process.pid, create=True).start()
        self.signal_action = self.exit_on_signal
        self.kill = mock.patch.object(disk.os, "killpg", side_effect=self.kill_group, create=True).start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(self.guard.stop)

    def exit_on_signal(self, pid, number):
        self.assertEqual(pid, self.process.pid)
        self.process.returncode = -number
        self.process.group_alive = False

    def kill_group(self, pid, number):
        if number == 0:
            if not self.process.group_alive:
                raise ProcessLookupError()
            return
        self.signal_action(pid, number)

    def signals(self):
        return [call.args for call in self.kill.call_args_list if call.args[1] != 0]

    def records(self):
        return [json.loads(line) for line in self.guard.path.read_text().splitlines()]

    def finish_watcher(self):
        self.guard._thread.join(timeout=2)
        self.assertFalse(self.guard._thread.is_alive())

    def test_low_space_preflight_prevents_command_attachment(self):
        self.space.return_value.free = 3 * 1024 ** 3 - 1
        with self.assertRaisesRegex(disk.DiskGuardError, "Disk reserve exhausted"):
            self.guard.preflight()
        with self.assertRaisesRegex(disk.DiskGuardError, "preflight must pass"):
            self.guard.start(self.process)
        self.kill.assert_not_called()
        record = self.records()[0]
        self.assertEqual(record["status"], "preflight-low-space")
        self.assertEqual(record["minimum_free_bytes"], 3 * 1024 ** 3)

    def test_unavailable_or_invalid_probe_fails_before_any_signal(self):
        for result in (OSError("probe unavailable"), SimpleNamespace(free=-1), SimpleNamespace(free=None)):
            guard = disk.DiskGuard(self.root, "native")
            self.space.side_effect = result if isinstance(result, Exception) else None
            self.space.return_value = result
            with self.subTest(result=result), self.assertRaisesRegex(disk.DiskGuardError, "probe failed"):
                guard.require_space()
        self.kill.assert_not_called()

    def test_monitor_detects_low_space_and_keeps_failed_result_after_child_exits(self):
        self.guard.preflight()
        self.space.return_value.free = 2 * 1024 ** 3
        self.guard.start(self.process)
        self.finish_watcher()
        self.guard.stop()
        self.assertEqual(self.signals(), [(self.process.pid, signal.SIGINT)])
        with self.assertRaisesRegex(disk.DiskGuardError, "Disk reserve exhausted"):
            self.guard.raise_if_failed()
        low = next(row for row in self.records() if row["status"] == "low-space")
        self.assertEqual(low["free_bytes"], 2 * 1024 ** 3)
        self.assertEqual(low["stage"], "capture")
        self.assertIn("+00:00", low["utc"])
        self.assertTrue(all(not ({"argv", "environment", "command"} & row.keys()) for row in self.records()))

    def test_group_mismatch_never_signals_another_group(self):
        self.guard.preflight()
        self.group.return_value = self.process.pid + 1
        with self.assertRaisesRegex(disk.DiskGuardError, "not its own process-group leader"):
            self.guard.start(self.process)
        self.guard.stop(terminate=True)
        self.kill.assert_not_called()
        with self.assertRaises(disk.DiskGuardError):
            self.guard.raise_if_failed()

    def test_caller_pid_is_never_accepted_as_owned_child(self):
        self.guard.preflight()
        self.process.pid = os.getpid()
        with self.assertRaisesRegex(disk.DiskGuardError, "caller process ID"):
            self.guard.start(self.process)
        self.kill.assert_not_called()

    def test_each_escalation_revalidates_group_and_stops_if_identity_changes(self):
        self.guard.preflight()
        self.signal_action = lambda *_: None  # The first signal does not stop the fake child.
        self.space.return_value.free = 0
        self.group.side_effect = lambda _: self.process.pid + 1 if self.signals() else self.process.pid
        self.guard.start(self.process)
        self.finish_watcher()
        self.assertEqual(self.signals(), [(self.process.pid, signal.SIGINT)])
        with self.assertRaisesRegex(disk.DiskGuardError, "changed process group"):
            self.guard.raise_if_failed()

    def test_stubborn_owned_process_gets_bounded_interrupt_term_kill(self):
        self.guard.preflight()
        self.space.return_value.free = 0
        def kill_only_last(pid, number):
            if number == getattr(signal, "SIGKILL", 9):
                self.exit_on_signal(pid, number)
        self.signal_action = kill_only_last
        self.guard.start(self.process)
        self.finish_watcher()
        self.assertEqual([number for _, number in self.signals()],
                         [signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGKILL", 9)])
        self.assertTrue(all(call.args == (self.process.pid,) for call in self.group.call_args_list))
        with self.assertRaises(disk.DiskGuardError):
            self.guard.raise_if_failed()

    def test_background_probe_or_unexpected_thread_error_surfaces_and_stops_child(self):
        for exception in (OSError("probe lost"), RuntimeError("unexpected worker error"), SystemExit("worker exited")):
            with self.subTest(exception=exception):
                guard = disk.DiskGuard(self.root, "build", interval_seconds=0.01,
                                      interrupt_timeout=0.02, terminate_timeout=0.02, kill_timeout=0.02)
                self.space.side_effect = None
                guard.preflight()
                self.process.returncode = None
                self.process.group_alive = True
                self.space.side_effect = exception
                guard.start(self.process)
                guard._thread.join(timeout=2)
                self.assertFalse(guard._thread.is_alive())
                guard.stop()
                with self.assertRaisesRegex(disk.DiskGuardError, "Disk watcher failed"):
                    guard.raise_if_failed()
                self.assertIsNotNone(self.process.returncode)

    def test_failed_evidence_write_does_not_block_owned_termination(self):
        self.guard.preflight()
        self.space.return_value.free = 0
        original = Path.open
        def open_file(path, *args, **kwargs):
            if path == self.guard.path:
                raise OSError("disk full")
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "open", open_file):
            with self.assertRaisesRegex(disk.DiskGuardError, "evidence write failed"):
                self.guard.start(self.process)
        self.assertEqual(self.signals(), [(self.process.pid, signal.SIGINT)])

    def test_normal_child_completion_and_stop_does_not_signal(self):
        self.guard.preflight()
        sampled = threading.Event()
        original = self.guard._record
        def record(status, **kwargs):
            result = original(status, **kwargs)
            if status == "sample":
                self.process.returncode = 0
                self.process.group_alive = False
                sampled.set()
            return result
        with mock.patch.object(self.guard, "_record", side_effect=record):
            self.guard.start(self.process)
            self.assertTrue(sampled.wait(timeout=2))
            self.finish_watcher()
            self.guard.stop()
        self.guard.raise_if_failed()
        self.assertEqual(self.signals(), [])
        self.assertIn("sample", [record["status"] for record in self.records()])

    def test_caller_abort_stops_only_owned_group_and_remains_failure(self):
        self.guard.preflight()
        self.guard.start(self.process)
        self.guard.stop(terminate=True)
        self.assertEqual(self.signals(), [(self.process.pid, signal.SIGINT)])
        with self.assertRaisesRegex(disk.DiskGuardError, "Caller stopped"):
            self.guard.raise_if_failed()

    def test_already_exited_process_is_never_signaled_even_on_abort(self):
        self.guard.preflight()
        self.process.returncode = 7
        self.process.group_alive = False
        self.guard.start(self.process)
        self.guard.stop(terminate=True)
        self.guard.raise_if_failed()
        self.kill.assert_not_called()
        self.group.assert_not_called()

    def test_timed_out_probe_join_fails_and_late_probe_cannot_kill_after_stop(self):
        self.guard.preflight()
        entered, resume = threading.Event(), threading.Event()
        def stalled_probe():
            entered.set()
            resume.wait(timeout=3)
            return 0
        with mock.patch.object(self.guard, "_free_bytes", side_effect=stalled_probe):
            self.guard.start(self.process)
            self.assertTrue(entered.wait(timeout=1))
            with mock.patch.object(self.guard._thread, "join"):
                self.guard.stop()
            with self.assertRaisesRegex(disk.DiskGuardError, "bounded join"):
                self.guard.raise_if_failed()
            resume.set()
            self.finish_watcher()
        self.kill.assert_not_called()

    def test_invalid_stage_and_reserve_cannot_create_evidence(self):
        for options in ({"stage": "../escape"}, {"stage": "build", "minimum_free_bytes": True},
                        {"stage": "build", "interval_seconds": float("inf")},
                        {"stage": "build", "kill_timeout": 0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                disk.DiskGuard(self.root, **options)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_captured_group_escalates_after_leader_exit_and_absence_is_latched(self):
        self.guard.preflight()
        self.space.return_value.free = 0
        def leader_exits_first(pid, number):
            self.process.returncode = -signal.SIGINT
            if number == getattr(signal, "SIGKILL", 9):
                self.process.group_alive = False
        self.signal_action = leader_exits_first
        self.guard.start(self.process)
        self.finish_watcher()
        self.assertEqual([number for _, number in self.signals()],
                         [signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGKILL", 9)])
        self.assertTrue(self.guard._group_absent)
        self.process.group_alive = True  # Simulate later numeric-ID reuse.
        count = len(self.kill.call_args_list)
        self.guard.stop(terminate=True)
        self.assertEqual(len(self.kill.call_args_list), count)

    def test_unconfirmed_group_drain_is_reported_after_all_bounded_signals(self):
        self.guard.preflight()
        self.space.return_value.free = 0
        self.signal_action = lambda *_: None
        self.guard.start(self.process)
        self.finish_watcher()
        self.assertEqual(len(self.signals()), 3)
        with self.assertRaisesRegex(disk.DiskGuardError, "group drain could not be confirmed"):
            self.guard.raise_if_failed()


@unittest.skipUnless(sys.platform == "linux", "Real process-group drain is exercised by Linux packaging CI")
class RealOwnedProcessGroupTests(unittest.TestCase):
    def test_exited_leader_stubborn_grandchild_is_reaped_and_other_session_survives(self):
        import ctypes
        # Become a temporary subreaper so this test, rather than container PID1,
        # can reap exactly its orphaned grandchild and distinguish zombies.
        libc = ctypes.CDLL(None, use_errno=True)
        old_subreaper = ctypes.c_int()
        self.assertEqual(libc.prctl(37, ctypes.byref(old_subreaper), 0, 0, 0), 0)
        self.assertEqual(libc.prctl(36, 1, 0, 0, 0), 0)
        leader = sentinel = guard = None
        grandchild = None
        reaper = None
        stop_reaper = threading.Event()
        reaped = threading.Event()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                pid_file, ready = root / "grandchild.pid", root / "leader.ready"
                child_code = ("import os,signal,time;from pathlib import Path;"
                              "signal.signal(signal.SIGINT,signal.SIG_IGN);"
                              "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                              "Path(" + repr(str(pid_file)) + ").write_text(str(os.getpid()));time.sleep(30)")
                leader_code = ("import signal,subprocess,sys,time;from pathlib import Path;"
                               "subprocess.Popen([sys.executable,'-c'," + repr(child_code) + "]);"
                               "signal.signal(signal.SIGINT,lambda *_:sys.exit(0));"
                               "Path(" + repr(str(ready)) + ").write_text('ready');time.sleep(30)")
                leader = subprocess.Popen([sys.executable, "-c", leader_code], start_new_session=True,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                sentinel = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"],
                                            start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                deadline = time.monotonic() + 5
                pid_text = ""
                while time.monotonic() < deadline:
                    pid_text = pid_file.read_text() if pid_file.exists() else ""
                    if pid_text.isdigit() and ready.exists():
                        break
                    time.sleep(0.01)
                self.assertTrue(pid_text.isdigit() and ready.exists())
                grandchild = int(pid_text)
                self.assertEqual(os.getpgid(grandchild), leader.pid)
                self.assertEqual(os.getsid(grandchild), leader.pid)

                def reap_exact_child():
                    while not stop_reaper.wait(0.01):
                        try:
                            pid, _ = os.waitpid(grandchild, os.WNOHANG)
                            if pid == grandchild:
                                reaped.set()
                                return
                        except ChildProcessError:
                            pass  # Still parented by the living leader.
                reaper = threading.Thread(target=reap_exact_child, daemon=True)
                reaper.start()
                guard = disk.DiskGuard(root, "real-child", interval_seconds=0.01,
                                       interrupt_timeout=1, terminate_timeout=0.2, kill_timeout=1)
                with mock.patch.object(disk.shutil, "disk_usage", return_value=SimpleNamespace(free=4 * 1024 ** 3)) as space:
                    guard.preflight()
                    space.return_value.free = 0
                    guard.start(leader)
                    guard._thread.join(timeout=4)
                    self.assertFalse(guard._thread.is_alive())
                    guard.stop(terminate=True)
                self.assertTrue(reaped.wait(timeout=1))
                self.assertEqual(leader.returncode, 0)  # Leader obeyed SIGINT first.
                self.assertIsNone(sentinel.poll())
                self.assertTrue(guard._group_absent)
                with self.assertRaisesRegex(disk.DiskGuardError, "Disk reserve exhausted") as error:
                    guard.raise_if_failed()
                self.assertNotIn("drain could not be confirmed", str(error.exception))
        finally:
            cleanup_errors = []
            def attempt(label, operation):
                try:
                    operation()
                except Exception as error:
                    cleanup_errors.append(label + ": " + type(error).__name__)
            def stop_leader():
                if leader is not None and leader.poll() is None:
                    try:
                        if os.getpgid(leader.pid) == leader.pid:
                            os.killpg(leader.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    leader.wait(timeout=3)
            def stop_grandchild():
                if grandchild is not None and not reaped.is_set():
                    try:
                        if os.getpgid(grandchild) == leader.pid:
                            os.kill(grandchild, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    if not reaped.wait(timeout=1):
                        cleanup_errors.append("grandchild: reaping was not confirmed")
            def stop_sentinel():
                if sentinel is not None:
                    sentinel.terminate()
                    try:
                        sentinel.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        sentinel.kill()
                        sentinel.wait(timeout=3)
            try:
                if guard is not None:
                    attempt("guard", lambda: guard.stop(terminate=True))
                attempt("leader", stop_leader)
                attempt("grandchild", stop_grandchild)
                stop_reaper.set()
                if reaper is not None:
                    attempt("reaper", lambda: reaper.join(timeout=1))
                    if reaper.is_alive():
                        cleanup_errors.append("reaper: bounded join expired")
                attempt("sentinel", stop_sentinel)
            finally:
                self.assertEqual(libc.prctl(36, old_subreaper.value, 0, 0, 0), 0)
            self.assertEqual(cleanup_errors, [])


if __name__ == "__main__":
    unittest.main()
