"""Reserve evidence space while supervising only an attached process group."""

import datetime
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import threading
import time


class DiskGuardError(RuntimeError):
    pass


class DiskGuard:
    """Call preflight, start(new-session Popen), stop, then raise_if_failed.

    The caller owns stdout/stderr and must stop(terminate=True) if it leaves
    while the command is alive. No command arguments or environment are saved.
    """

    def __init__(self, artifacts, stage, *, minimum_free_bytes=3 * 1024 ** 3,
                 interval_seconds=5, interrupt_timeout=10, terminate_timeout=5,
                 kill_timeout=5):
        if not isinstance(stage, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", stage):
            raise ValueError("Invalid disk guard stage")
        if type(minimum_free_bytes) is not int or minimum_free_bytes <= 0:
            raise ValueError("Disk reserve must be a positive integer")
        for value in (interval_seconds, interrupt_timeout, terminate_timeout, kill_timeout):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 10:
                raise ValueError("Disk guard intervals must be finite, positive, and at most ten seconds")
        self.artifacts = Path(artifacts).resolve(strict=True)
        if not self.artifacts.is_dir():
            raise ValueError("Disk guard evidence directory must exist")
        self.stage = stage
        self.minimum_free_bytes = minimum_free_bytes
        self.interval_seconds = interval_seconds
        self.timeouts = (interrupt_timeout, terminate_timeout, kill_timeout)
        self.path = self.artifacts / ("disk-guard-" + stage + ".jsonl")
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._termination_lock = threading.Lock()
        self._failures = []
        self._process = None
        self._pgid = None
        self._group_absent = False
        self._thread = None
        self._preflight_passed = False

    def _fail(self, message):
        with self._lock:
            if message not in self._failures:
                self._failures.append(message)

    def _record(self, status, **fields):
        record = {"schema_version": 1, "stage": self.stage,
                  "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  "status": status, "minimum_free_bytes": self.minimum_free_bytes, **fields}
        try:
            with self._lock:
                with self.path.open("a", encoding="utf-8", newline="\n") as destination:
                    destination.write(json.dumps(record, separators=(",", ":")) + "\n")
        except Exception as error:
            # A full disk must not prevent stopping the owned command.
            self._fail("Disk guard evidence write failed (" + type(error).__name__ + ")")
            return False
        return True

    def _free_bytes(self):
        free = shutil.disk_usage(self.artifacts).free
        if type(free) is not int or free < 0:
            raise ValueError("Invalid free-space probe result")
        return free

    def require_space(self):
        """Fail before command creation if space or its evidence is unavailable."""
        try:
            free = self._free_bytes()
        except Exception as error:
            self._fail("Disk space probe failed (" + type(error).__name__ + ")")
            self._record("preflight-probe-failed", error_type=type(error).__name__)
            self.raise_if_failed()
        adequate = free >= self.minimum_free_bytes
        self._record("preflight-passed" if adequate else "preflight-low-space", free_bytes=free)
        if not adequate:
            self._fail(f"Disk reserve exhausted: {free} free bytes; {self.minimum_free_bytes} required")
        self.raise_if_failed()
        self._preflight_passed = True
        return free

    preflight = require_space

    def _claim_group(self):
        process = self._process
        if process.poll() is not None:
            return False
        pid = process.pid
        if type(pid) is not int or pid <= 1 or pid == os.getpid():
            raise DiskGuardError("Refusing to signal an invalid or caller process ID")
        getpgid = getattr(os, "getpgid", None)
        if getpgid is None or not callable(getattr(os, "killpg", None)):
            raise DiskGuardError("Owned process-group supervision requires POSIX process APIs")
        try:
            pgid = getpgid(pid)
        except ProcessLookupError:
            if process.poll() is not None:
                return False
            raise DiskGuardError("Attached process group disappeared before ownership verification") from None
        if pgid != pid:
            raise DiskGuardError("Attached command is not its own process-group leader; refusing to signal")
        self._pgid = pid
        return True

    def _group_alive(self):
        if self._pgid is None or self._group_absent:
            return False
        if self._process.poll() is None:
            try:
                if os.getpgid(self._pgid) != self._pgid:
                    raise DiskGuardError("Attached command changed process group; refusing to signal")
                return True
            except ProcessLookupError:
                # Popen.poll() can return None while another thread holds its
                # waitpid lock, even after that thread reaped the leader. Once
                # ownership is established, probe ONLY the captured group below;
                # cached process status cannot decide whether descendants remain.
                pass
        # The leader can exit on SIGINT before its children. The captured group
        # remains task-owned until it disappears. Never discover another group,
        # and never signal this numeric ID again after observing its absence.
        try:
            os.killpg(self._pgid, 0)
        except ProcessLookupError:
            self._group_absent = True
            return False
        return True

    def _wait_group(self, timeout):
        deadline = time.monotonic() + timeout
        while self._group_alive():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.05, remaining))
        return True

    def start(self, process):
        if not self._preflight_passed:
            raise DiskGuardError("Disk guard preflight must pass before attaching a command")
        if self._process is not None or self._stop.is_set():
            raise DiskGuardError("Disk guard instances supervise only one command")
        self._process = process
        try:
            alive = self._claim_group()
        except Exception as error:
            self._fail(str(error) if isinstance(error, DiskGuardError) else "Process-group verification failed (" + type(error).__name__ + ")")
            self._record("ownership-rejected", pid=getattr(process, "pid", None))
            self.raise_if_failed()
        if not self._record("attached" if alive else "already-exited", pid=process.pid):
            if alive:
                self._terminate_owned()
            self.raise_if_failed()
        if alive:
            self._thread = threading.Thread(target=self._watch, name="disk-guard-" + self.stage, daemon=True)
            try:
                self._thread.start()
            except Exception as error:
                self._fail("Disk watcher could not start (" + type(error).__name__ + ")")
                self._terminate_owned()
                self.raise_if_failed()

    def _terminate_owned(self):
        if not self._termination_lock.acquire(timeout=sum(self.timeouts) + 1):
            self._fail("Concurrent owned-process termination did not finish within its bound")
            return
        try:
            for number, label, timeout in zip((signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGKILL", 9)),
                                              ("SIGINT", "SIGTERM", "SIGKILL"), self.timeouts):
                try:
                    # Revalidate the living leader or probe ONLY the captured
                    # group immediately before each escalation.
                    self._record("signal-requested", pgid=self._pgid, signal=label)
                    if not self._group_alive():
                        return
                    os.killpg(self._pgid, number)
                    if self._wait_group(timeout):
                        self._record("stopped", pgid=self._pgid, signal=label)
                        return
                    self._record("signal-timeout", pgid=self._pgid, signal=label)
                except Exception as error:
                    self._fail(str(error) if isinstance(error, DiskGuardError) else "Owned-process termination failed (" + type(error).__name__ + ")")
                    self._record("termination-failed", error_type=type(error).__name__)
                    return
            self._fail("Owned process group drain could not be confirmed after bounded SIGKILL wait")
        finally:
            self._termination_lock.release()

    def _watch(self):
        try:
            while not self._stop.is_set():
                if not self._group_alive():
                    self._record("group-exited", pgid=self._pgid)
                    return
                free = self._free_bytes()
                if self._stop.is_set():
                    return
                if free < self.minimum_free_bytes:
                    self._fail(f"Disk reserve exhausted: {free} free bytes; {self.minimum_free_bytes} required")
                    self._record("low-space", free_bytes=free)
                    self._terminate_owned()
                    return
                if not self._record("sample", free_bytes=free):
                    self._terminate_owned()
                    return
                self._stop.wait(self.interval_seconds)
        except BaseException as error:
            # Our own errors contain fixed ownership messages, never argv/env.
            # Retain their reason so an actual runner failure is diagnosable.
            detail = str(error) if type(error) is DiskGuardError else None
            self._fail("Disk watcher failed (" + type(error).__name__ + (": " + detail if detail else "") + ")")
            self._record("watcher-failed", error_type=type(error).__name__, guard_error=detail)
            if not self._stop.is_set():
                self._terminate_owned()

    def stop(self, *, terminate=False):
        """Stop sampling; optionally abort the still-running owned command."""
        self._stop.set()
        if self._process is not None and self._pgid is not None:
            try:
                if terminate and self._group_alive():
                    self._fail("Caller stopped an unfinished guarded command")
                    self._terminate_owned()
                elif self._process.poll() is not None and self._group_alive():
                    self._fail("Owned process group outlived its completed command")
                    self._terminate_owned()
            except Exception as error:
                self._fail("Caller cleanup failed (" + type(error).__name__ + ")")
        if self._thread is not None and self._thread.ident is not None:
            self._thread.join(timeout=sum(self.timeouts) + 2)
            if self._thread.is_alive():
                self._fail("Disk watcher did not stop within its bounded join")
        self._record("guard-stopped")

    def raise_if_failed(self):
        with self._lock:
            failures = list(self._failures)
        if failures:
            raise DiskGuardError("; ".join(failures))
