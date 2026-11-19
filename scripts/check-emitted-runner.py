#!/usr/bin/env python3
"""Linux-only compatibility probe for actual Bazel 9.2 generated test scripts.

This runs a tiny generic test rule. It provides no iOS/native feature evidence.
The release checksum is published at the official URL below plus '.sha256'.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

import emitted_test_runner as emitted
from disk_guard import DiskGuard
import package


URL = "https://github.com/bazelbuild/bazel/releases/download/9.2.0/bazel-9.2.0-linux-x86_64"
SHA256 = "7668a95db1250f12c40407251e4e203b4ec8bf39bc495d2f485b2d8c99048694"
UDID = "CA1C775E-9033-4666-9B09-1E195CA035B5"


def download(destination):
    started, total = time.monotonic(), 0
    hasher = hashlib.sha256()
    with urllib.request.urlopen(URL, timeout=30) as response, destination.open("xb") as output:
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > 128 * 1024 ** 2 or time.monotonic() - started > 120:
                raise package.PackageError("Pinned Bazel download exceeded its bound")
            hasher.update(block)
            output.write(block)
    if hasher.hexdigest() != SHA256:
        raise package.PackageError("Official Linux Bazel binary differs from the pinned release SHA-256")
    destination.chmod(0o700)


def emit(binary, workspace, private, output_user_root, target, selection, artifacts, index):
    script = private / ("test-" + str(index) + ".sh")
    command = [str(binary), "--batch", "--output_user_root=" + str(output_user_root), "run",
               "--script_path=" + str(script), "--compilation_mode=dbg",
               "--test_arg=--destination=platform=ios_simulator,id=" + UDID]
    if selection is not None:
        command.append("--test_filter=" + selection)
    command.append(target)
    # This launcher is our own fixed argv, not a reconstruction of the emitted
    # test command. Gate ownership before starting the bounded batch build.
    launcher = private / ("emit-" + str(index) + ".sh")
    launcher.write_text("#!/bin/bash\nexec " + shlex.join(command) + "\n", encoding="utf-8")
    launcher.chmod(0o700)
    guard = DiskGuard(artifacts, "probe-emission-" + str(index))
    guard.preflight()
    read_fd, release_fd = os.pipe()
    process, failure, output = None, None, b""
    previous_signals = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    def terminate_requested(number, _frame):
        raise SystemExit(128 + number)
    signal.signal(signal.SIGTERM, terminate_requested)
    try:
        process = subprocess.Popen([sys.executable, str(Path(emitted.__file__).resolve()), "--exec-gate",
                                    str(read_fd), str(launcher)], cwd=workspace, pass_fds=(read_fd,),
                                   start_new_session=True, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        os.close(read_fd)
        read_fd = None
        guard.start(process)
        os.write(release_fd, b"1")
        os.close(release_fd)
        release_fd = None
        output, _ = process.communicate(timeout=300)
        if process.returncode != 0:
            failure = "Bazel probe emission exited " + str(process.returncode)
    except BaseException as error:
        failure = "Bazel probe emission failed (" + type(error).__name__ + ")"
        if isinstance(error, subprocess.TimeoutExpired):
            output = error.output or b""
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        for number in previous_signals:
            signal.signal(number, signal.SIG_IGN)
        try:
            for descriptor in (read_fd, release_fd):
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            try:
                guard.stop(terminate=failure is not None or (process is not None and process.poll() is None))
                guard.raise_if_failed()
            except Exception as error:
                failure = (failure + "; " if failure else "") + str(error)
            if process is not None:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    failure = (failure + "; " if failure else "") + "Batch emission did not finish cleanup"
                if process.stdout is not None:
                    process.stdout.close()
            # No private emitted script or environment is uploaded; only Bazel's
            # output from this fixture with no owner configuration or credentials.
            (artifacts / ("probe-emission-" + str(index) + ".log")).write_bytes(output[:8 * 1024 ** 2])
        finally:
            for number, handler in previous_signals.items():
                signal.signal(number, handler)
    if failure:
        raise package.PackageError(failure)
    script.chmod(0o700)  # Same permission step as Make.test --scriptPath.
    return script


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", required=True, type=Path)
    args = parser.parse_args(argv)
    args.artifacts.mkdir(parents=True, exist_ok=True)
    artifacts = args.artifacts.resolve(strict=True)
    report = {"schema_version": 1, "scope": "Linux generic emitted-test compatibility, not iOS/native tests",
              "bazel_version": "9.2.0", "bazel_sha256": SHA256, "status": "failed", "targets": []}
    try:
        if sys.platform != "linux" or platform.machine() not in ("x86_64", "AMD64"):
            raise package.PackageError("Actual emitted-runner probe requires Linux x86_64")
        with tempfile.TemporaryDirectory(prefix="draft-pocket-emitted-") as temporary:
            root = Path(temporary).resolve()
            if root.is_relative_to(artifacts):
                raise package.PackageError("Private probe workspace must be outside uploaded artifacts")
            binary, workspace, private = root / "bazel", root / "workspace", root / "private"
            workspace.mkdir()
            private.mkdir(mode=0o700)
            download(binary)
            (workspace / "MODULE.bazel").write_text('module(name = "draft_pocket_probe")\n', encoding="utf-8")
            (workspace / "Probe").mkdir()
            (workspace / "Probe/probe.bzl").write_text(
                'def _probe_impl(ctx):\n'
                '    executable = ctx.actions.declare_file(ctx.label.name)\n'
                '    ctx.actions.write(executable, "#!/bin/bash\\nprintf \'EMITTED_PROBE_EXECUTED\\\\n\'\\n", is_executable = True)\n'
                '    return [DefaultInfo(executable = executable)]\n'
                'probe_test = rule(implementation = _probe_impl, test = True)\n', encoding="utf-8")
            (workspace / "Probe/BUILD").write_text(
                'load(":probe.bzl", "probe_test")\nprobe_test(name = "Selected")\nprobe_test(name = "Unfiltered")\n', encoding="utf-8")
            for index, (name, selection) in enumerate((("Selected", "ProbeCase/testOne"), ("Unfiltered", None)), 1):
                target = "//Probe:" + name
                script = emit(binary, workspace, private, root / "output-user-root", target, selection, artifacts, index)
                plan = emitted.validate_emitted_script(workspace, script, target=target, test_filter=selection, udid=UDID)
                lines = []
                result = emitted.run_emitted(plan, artifacts, stage="probe-test-" + str(index), timeout_seconds=30,
                                             on_line=lines.append)
                passed = (result.returncode == 0 and not result.timed_out and not result.supervision_errors
                          and sum(line.strip() == "EMITTED_PROBE_EXECUTED" for line in lines) == 1)
                report["targets"].append({**plan.identity, "returncode": result.returncode,
                                          "timed_out": result.timed_out, "supervision_errors": result.supervision_errors,
                                          "status": "passed" if passed else "failed"})
                if not passed:
                    raise package.PackageError("Actual generic emitted test did not satisfy its exact execution proof")
            report["status"] = "passed"
        return 0
    except Exception as error:
        report["error"] = str(error) if isinstance(error, package.PackageError) else type(error).__name__
        return 1
    finally:
        (artifacts / "emitted-runner-probe.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
