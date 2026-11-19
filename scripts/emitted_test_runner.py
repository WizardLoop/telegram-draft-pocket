"""Validate and supervise Bazel 9.2's emitted test script, without restarting Bazel.

Grammar: bazelbuild/bazel@9.2.0 RunCommandLine.java:196-223 and ShellEscaper.java.
Test identity: RunCommand.java:754-844, TestRunnerAction.java:724-794.
The BUILD_EXECROOT branch in tools/test/test-setup.sh skips Bazel supervision.
Native test-result verification remains the caller's responsibility.
"""

import codecs
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid

import package
from disk_guard import DiskGuard


UNSET = ("JAVA_RUNFILES", "RUNFILES_DIR", "RUNFILES_MANIFEST_FILE", "RUNFILES_MANIFEST_ONLY", "TEST_SRCDIR")
SETUP = "external/bazel_tools/tools/test/test-setup.sh"
SETUP_SHA256 = "49ba08927c3c556c52c6f771eaca362a0dbd1b6e19fd2667c61d92c33a32278a"
SEPARATOR = " \\\n    "


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _escape(value):
    # Match Bazel ShellEscaper, which intentionally quotes '=' and shell syntax.
    if value and not value.startswith("~") and re.fullmatch(r"[A-Za-z0-9@%_+:,./~\-]+", value):
        return value
    return "'" + value.replace("'", "'\\''") + "'"


def _format(shell, cwd, environment, arguments):
    return ("#!" + shell + "\ncd " + _escape(cwd) + " && \\\n  exec env \\\n    "
            + SEPARATOR.join("-u " + key for key in UNSET) + SEPARATOR
            + SEPARATOR.join(_escape(key) + "=" + _escape(value)
                             for key, value in sorted(environment.items()))
            + " \\\n  " + " ".join(_escape(value) for value in arguments) + ' "$@"')


def _parse(data):
    if len(data) > 1024 ** 2 or b"\0" in data:
        raise package.PackageError("Emitted test script is invalid or exceeds 1 MiB")
    try:
        source = data.decode("latin-1")  # RunCommand.writeScript uses ISO_8859_1.
        shell, body = source.split("\n", 1)
        if shell not in ("#!/bin/bash", "#!/bin/sh") or not body.endswith(' "$@"'):
            raise ValueError()
        tokens = shlex.split(body[:-5].replace("\\\n", ""), posix=True)
        if tokens[:1] != ["cd"] or tokens[2:5] != ["&&", "exec", "env"]:
            raise ValueError()
        index = 5
        for key in UNSET:
            if tokens[index:index + 2] != ["-u", key]:
                raise ValueError()
            index += 2
        environment = {}
        while index < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[index]):
            key, value = tokens[index].split("=", 1)
            if key in environment or any(ord(char) < 32 for char in value):
                raise ValueError()
            environment[key] = value
            index += 1
        arguments = tokens[index:]
        if not arguments or _format(shell[2:], tokens[1], environment, arguments) != source:
            raise ValueError()
        return tokens[1], environment, arguments
    except (ValueError, IndexError):
        raise package.PackageError("Unsupported or altered Bazel 9.2 emitted-script grammar") from None


def _plain_file(path, *, limit=None):
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)() or not path.is_file():
        raise package.PackageError("Emitted test identity requires a regular file")
    if limit is not None and path.stat().st_size > limit:
        raise package.PackageError("Emitted test identity file exceeds its size bound")
    return path


def _relative_output(execroot, output_root, value):
    relative = package.relative(value)
    if not relative.startswith("bazel-out/"):
        raise package.PackageError("Emitted test output is outside Bazel outputs")
    if (execroot / "bazel-out").resolve(strict=True) != output_root:
        raise package.PackageError("Emitted script uses another Bazel execution root")
    return package.bounded(output_root, relative[len("bazel-out/"):])


def _fresh(directory):
    if directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
        raise package.PackageError("Emitted native output directory is a link")
    if directory.exists() and (not directory.is_dir() or next(directory.iterdir(), None) is not None):
        raise package.PackageError("Emitted native output is not empty; preserve the prior take before a fresh emission")


def _manifest_runner(manifest, logical, runner):
    matches = []
    def unescape(value):
        if re.search(r"\\(?![snb])", value):
            raise package.PackageError("Invalid emitted runfiles escape")
        return re.sub(r"\\([snb])", lambda match: {"s": " ", "n": "\n", "b": "\\"}[match[1]], value)
    for line in manifest.read_text(encoding="utf-8").splitlines():
        escaped = line.startswith(" ")
        key, separator, value = (line[1:] if escaped else line).partition(" ")
        if escaped:
            key = unescape(key)
        if key == logical:
            if not separator:
                raise package.PackageError("Emitted runner manifest lacks a physical path")
            matches.append(unescape(value) if escaped else value)
    if (len(matches) != 1 or not Path(matches[0]).is_absolute()
            or Path(matches[0]).resolve(strict=True) != runner):
        raise package.PackageError("Emitted runner manifest does not uniquely match the current target")


@dataclass(frozen=True)
class EmittedPlan:
    checkout: Path
    script_path: Path
    output_directory: Path
    test_directory: Path
    target: str
    test_filter: object
    udid: str
    identity: dict


def validate_emitted_script(checkout, script_path, *, target, test_filter, udid):
    """Accept only this checkout's exact target, filter, destination and fresh outputs."""
    try:
        if not re.fullmatch(r"//[A-Za-z0-9_/.-]+:[A-Za-z0-9_.-]+", target):
            raise package.PackageError("Emitted tests require a main-repository target")
        target_path = package.relative(target[2:].replace(":", "/"))
        if test_filter is not None and (not isinstance(test_filter, str) or not re.fullmatch(r"[A-Za-z0-9_./]+", test_filter)):
            raise package.PackageError("Emitted tests require an exact class or method filter")
        canonical_udid = str(uuid.UUID(udid)).upper()
        if udid != canonical_udid:
            raise package.PackageError("Emitted tests require the canonical owned Simulator UUID")
        checkout = Path(checkout).resolve(strict=True)
        script = Path(script_path)
        if not script.is_absolute() or script.parent.is_symlink():
            raise package.PackageError("Emitted script requires an absolute private path")
        _plain_file(script, limit=1024 ** 2)
        if os.name == "posix":
            if (script.stat().st_uid != os.getuid() or script.parent.stat().st_uid != os.getuid()
                    or stat.S_IMODE(script.stat().st_mode) != 0o700
                    or stat.S_IMODE(script.parent.stat().st_mode) & 0o077):
                raise package.PackageError("Emitted script and parent must be private and owned")
        script = script.resolve(strict=True)
        raw = script.read_bytes()
        cwd, env, args = _parse(raw)
        if not Path(cwd).is_absolute():
            raise package.PackageError("Emitted test execution root is relative")
        execroot = Path(cwd).resolve(strict=True)
        if (env.get("BUILD_EXECROOT") != cwd or execroot.name != "_main"
                or execroot.parent.name != "execroot"
                or Path(env.get("BUILD_WORKSPACE_DIRECTORY", "")).resolve(strict=True) != checkout
                or Path(env.get("BUILD_WORKING_DIRECTORY", "")).resolve(strict=True) != checkout):
            raise package.PackageError("Emitted test workspace/execution identity differs")
        if (env.get("TEST_TARGET") != target or env.get("TEST_BINARY") != target_path
                or env.get("TEST_WORKSPACE") != "_main"
                or (test_filter is None and "TESTBRIDGE_TEST_ONLY" in env)
                or (test_filter is not None and env.get("TESTBRIDGE_TEST_ONLY") != test_filter)):
            raise package.PackageError("Emitted test target/filter identity differs")
        if any(key in env for key in ("BASH_ENV", "ENV", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES",
                                     "COVERAGE_DIR", "RUNTEST_PRESERVE_CWD", "TEST_TOTAL_SHARDS")):
            raise package.PackageError("Emitted test has unsupported execution overrides")
        destination = "--destination=platform=ios_simulator,id=" + canonical_udid
        if args != [SETUP, target_path, destination]:
            raise package.PackageError("Emitted test command/destination differs")
        setup = (execroot / SETUP).resolve(strict=True)
        if _hash(_plain_file(setup)) != SETUP_SHA256:
            raise package.PackageError("Emitted test setup differs from pinned Bazel 9.2")
        output_root = (checkout / "bazel-out").resolve(strict=True)
        runner = _plain_file(package.bounded(checkout / "bazel-bin", target_path), limit=2 * 1024 ** 2)
        runfiles = Path(str(runner) + ".runfiles")
        if not runfiles.is_dir():
            raise package.PackageError("Emitted test runfiles directory is missing")
        for key in ("TEST_SRCDIR", "RUNFILES_DIR", "JAVA_RUNFILES", "PYTHON_RUNFILES"):
            if _relative_output(execroot, output_root, env.get(key, "")).resolve(strict=True) != runfiles.resolve(strict=True):
                raise package.PackageError("Emitted test runfiles identity differs")
        manifest = _plain_file(package.bounded(runner.parent, runner.name + ".runfiles_manifest"), limit=64 * 1024 ** 2)
        _manifest_runner(manifest, "_main/" + target_path, runner)
        if (runfiles / "_main" / target_path).resolve(strict=True) != runner:
            raise package.PackageError("Emitted target is absent from the prepared runfiles tree")
        output = _relative_output(execroot, output_root, env.get("TEST_UNDECLARED_OUTPUTS_DIR", ""))
        relative = output.relative_to(output_root).as_posix()
        if not re.fullmatch(r"[^/]+/testlogs/" + re.escape(target_path) + r"/test.outputs", relative):
            raise package.PackageError("Emitted native output belongs to another target")
        for key, value in env.items():
            if (key.startswith("TEST_") and (key.endswith("_FILE") or key.startswith("TEST_UNDECLARED_OUTPUTS_"))) or key == "XML_OUTPUT_FILE":
                path = _relative_output(execroot, output_root, value)
                if not path.is_relative_to(output.parent):
                    raise package.PackageError("Emitted result path escapes its target")
        if not re.fullmatch(r"_tmp/[0-9a-f]{32}", env.get("TEST_TMPDIR", "")):
            raise package.PackageError("Emitted temporary directory differs from the pinned default")
        package.bounded(execroot, env["TEST_TMPDIR"])
        _fresh(output)
        identity = {"schema_version": 1, "source": "bazel-9.2-run-script", "target": target,
                    "test_filter": test_filter, "udid": canonical_udid,
                    "script_sha256": hashlib.sha256(raw).hexdigest(), "test_setup_sha256": SETUP_SHA256,
                    "test_runner_sha256": _hash(runner), "runfiles_manifest_sha256": _hash(manifest),
                    "output_directory": str(output), "output_fresh": True}
        return EmittedPlan(checkout, script, output, output.parent, target, test_filter, canonical_udid, identity)
    except (OSError, ValueError, TypeError) as error:
        if isinstance(error, package.PackageError):
            raise
        raise package.PackageError("Cannot validate emitted test identity (" + type(error).__name__ + ")") from None


@dataclass(frozen=True)
class EmittedResult:
    returncode: int
    timed_out: bool
    supervision_errors: list
    output_directory: Path


def run_emitted(plan, artifacts, *, stage, timeout_seconds, on_line):
    """Run only the validated script; callback output is caller-redacted before storage."""
    if os.name != "posix":
        raise package.PackageError("Emitted native tests require POSIX process-group supervision")
    if threading.current_thread() is not threading.main_thread():
        raise package.PackageError("Emitted tests must run on the main thread for cancellation cleanup")
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 3600):
        raise package.PackageError("Emitted test deadline must be positive and at most one hour")
    artifacts = Path(artifacts).resolve(strict=True)
    if plan.script_path.is_relative_to(artifacts):
        raise package.PackageError("Private emitted script must be outside uploaded artifacts")
    current = validate_emitted_script(plan.checkout, plan.script_path, target=plan.target,
                                     test_filter=plan.test_filter, udid=plan.udid)
    if current.identity != plan.identity:
        raise package.PackageError("Emitted script or its declared inputs changed after validation")
    guard = DiskGuard(artifacts, stage)
    guard.preflight()
    try:
        descriptor = os.open(str(plan.script_path) + ".started", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
    except FileExistsError:
        raise package.PackageError("Emitted script has already been used; create a fresh emission") from None
    process = None
    read_fd, release_fd = os.pipe()
    selector = selectors.DefaultSelector()
    errors, timed_out, primary = [], False, None
    decoder, pending = codecs.getincrementaldecoder("utf-8")("replace"), ""
    started = time.monotonic()
    previous_signals = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    def terminate_requested(number, _frame):
        raise SystemExit(128 + number)
    signal.signal(signal.SIGTERM, terminate_requested)
    try:
        # Gate exec until the parent has verified ownership. Closing release_fd
        # before releasing causes the child to exit without starting the runner.
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--exec-gate",
                                    str(read_fd), str(plan.script_path)], pass_fds=(read_fd,),
                                   start_new_session=True, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
        os.close(read_fd)
        read_fd = None
        guard.start(process)
        os.write(release_fd, b"1")
        os.close(release_fd)
        release_fd = None
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            guard.raise_if_failed()
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                timed_out = True
                errors.append("Emitted test exceeded its overall deadline")
                break
            events = selector.select(min(0.2, remaining))
            if not events:
                if process.poll() is not None:
                    break
                continue
            data = os.read(process.stdout.fileno(), 65536)
            if not data:
                # EOF can precede waitpid becoming ready. Keep the overall
                # deadline while waiting for the actual process exit.
                selector.unregister(process.stdout)
                continue
            pending += decoder.decode(data)
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                on_line(line + "\n")
            if len(pending) > 1024 ** 2:
                raise package.PackageError("Emitted test output line exceeds 1 MiB")
        pending += decoder.decode(b"", final=True)
        if pending:
            on_line(pending)
    except BaseException as error:
        primary = error
        errors.append("Emitted test supervision failed (" + type(error).__name__ + ")")
    finally:
        # A second cancellation must not interrupt the bounded group drain.
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
                guard.stop(terminate=bool(errors) or (process is not None and process.poll() is None))
                guard.raise_if_failed()
            except Exception as error:
                errors.append(str(error))
            if process is not None:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    errors.append("Owned emitted runner did not exit after bounded cleanup")
                except OSError:
                    errors.append("Owned emitted runner wait failed")
                if process.stdout is not None:
                    try:
                        process.stdout.close()
                    except OSError:
                        errors.append("Owned emitted output could not be closed")
            try:
                selector.close()
            except OSError:
                errors.append("Emitted output selector could not be closed")
            code = process.returncode if process is not None and process.returncode is not None else -1
            record = {**plan.identity, "returncode": code, "timed_out": timed_out,
                      "supervision_errors": errors, "elapsed_seconds": round(time.monotonic() - started, 3)}
            try:
                (artifacts / ("emitted-" + stage + ".json")).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            except OSError:
                errors.append("Emitted runner evidence could not be written")
        finally:
            for number, handler in previous_signals.items():
                signal.signal(number, handler)
    if isinstance(primary, (KeyboardInterrupt, SystemExit)):
        raise primary
    return EmittedResult(code, timed_out, errors, plan.output_directory)


def _exec_gate(arguments):
    if len(arguments) != 3 or arguments[0] != "--exec-gate":
        return 2
    descriptor = int(arguments[1])
    try:
        released = os.read(descriptor, 1) == b"1"
    finally:
        os.close(descriptor)
    if not released:
        return 125
    os.execv(arguments[2], [arguments[2]])
    return 126


if __name__ == "__main__":
    raise SystemExit(_exec_gate(sys.argv[1:]))
