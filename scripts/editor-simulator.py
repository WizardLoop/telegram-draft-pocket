#!/usr/bin/env python3
"""Manual, credential-free iOS lane for the three legacy editor selection tests."""

import argparse
import datetime
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import time

import package
from simulator import SimulatorSession


ROOT = Path(__file__).resolve().parent.parent
EDITOR = "submodules/TelegramUI/Components/RichTextEditor"
LOCAL_DEPENDENCY = "submodules/MosaicLayout"
FILTER = "RichTextEditorUIKitTests/DocumentTextSelectionTests"
EXPECTED_METHODS = {
    "testNativeLegacyCellCaretRestoresAndTypesInTheSameCell",
    "testReversedNativeLegacyRangeKeepsBothEndpoints",
    "testInvalidOffsetDoesNotMoveNativeSelection",
}
CRASH = re.compile(r"Fatal error:|Restarting after unexpected exit|Early unexpected exit|"
                   r"Lost connection to the test runner|Test runner[^\n]*(?:crashed|unexpectedly)|"
                   r"(?:exited|terminated) (?:with|due to)[^\n]*(?:signal|crash)", re.IGNORECASE)

# Share the already-tested native xcresult schema, without invoking capture.
_spec = importlib.util.spec_from_file_location("editor_native_schema", ROOT / "scripts/capture-demo.py")
_schema = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_schema)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def authorized_event(event, event_name, repository):
    def field(node, *keys):
        for key in keys:
            node = node.get(key) if isinstance(node, dict) else None
        return node
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        return False
    if field(event, "repository", "private") is not False or field(event, "repository", "full_name") != repository:
        return False
    if event_name == "workflow_dispatch":
        verified = field(event, "inputs", "included_usage_verified")
        return field(event, "inputs", "lane") == "editor" and (verified is True or verified == "true")
    if event_name == "pull_request":
        return (field(event, "action") == "labeled"
                and field(event, "label", "name") == "run-editor-simulator"
                and field(event, "pull_request", "head", "repo", "full_name") == repository)
    return False


def hosted_paths(artifacts):
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        raise package.PackageError("Editor CI requires the explicitly requested standard GitHub-hosted lane")
    if os.environ.get("DRAFT_POCKET_BUILD_CONFIGURATION_JSON"):
        raise package.PackageError("Editor CI must not receive an app configuration or credentials")
    event_path = Path(os.environ.get("GITHUB_EVENT_PATH", ""))
    if not event_path.is_file() or event_path.stat().st_size > 2 * 1024 * 1024:
        raise package.PackageError("Missing or oversized workflow event identity")
    event = package.load_json(event_path)
    if not authorized_event(event, os.environ.get("GITHUB_EVENT_NAME"), os.environ.get("GITHUB_REPOSITORY")):
        raise package.PackageError("Editor CI requires a public same-repository manual request")
    runner_temp = Path(os.environ.get("RUNNER_TEMP", ""))
    if not runner_temp.is_absolute() or not runner_temp.is_dir():
        raise package.PackageError("Editor CI requires an existing absolute RUNNER_TEMP")
    temporary, artifacts = runner_temp.resolve(), Path(artifacts).resolve()
    if artifacts == temporary or not artifacts.is_relative_to(temporary):
        raise package.PackageError("Editor artifacts must stay inside RUNNER_TEMP")
    return temporary, artifacts


class Commands:
    def __init__(self, artifacts):
        self.artifacts, self.events = artifacts, []

    def run(self, name, command, *, cwd=None, timeout=60):
        """Keep raw output and stop only this command's own process group on timeout."""
        record = {"name": name, "command": [str(x) for x in command], "started_utc": now(),
                  "timeout_seconds": timeout, "status": "running", "log": name + ".log"}
        self.events.append(record)
        started = time.monotonic()
        print("Editor stage: " + name, flush=True)
        try:
            with (self.artifacts / record["log"]).open("xb") as log:
                process = subprocess.Popen([str(x) for x in command], cwd=cwd,
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    # This PID is the new session/group created above, not a
                    # global tool-name match or an unrelated Simulator process.
                    if os.name == "posix":
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                    else:
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        if os.name == "posix":
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        else:
                            process.kill()
                        process.wait(timeout=5)
                    record["status"] = "timed_out"
                    raise package.PackageError(f"{name} exceeded {timeout}s; raw log retained") from None
            record["returncode"] = code
            if code:
                raise package.PackageError(f"{name} exited {code}; raw log retained")
            record["status"] = "passed"
        except (package.PackageError, OSError) as error:
            if record["status"] == "running":
                record["status"] = "failed"
            record["error"] = str(error)
            raise
        finally:
            record.update(finished_utc=now(), elapsed_seconds=round(time.monotonic() - started, 3))
            (self.artifacts / "commands.json").write_text(json.dumps(self.events, indent=2) + "\n", encoding="utf-8")
        return self.artifacts / record["log"]


def pinned_toolchain(base, commands):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise package.PackageError("Native UIKit tests require the pinned Apple Silicon macOS runner")
    developer = Path("/Applications") / f"Xcode_{base['versions']['xcode']}.app/Contents/Developer"
    if not developer.is_dir():
        raise package.PackageError("Pinned Xcode is unavailable; no fallback or override is allowed")
    os.environ["DEVELOPER_DIR"] = str(developer)
    macos = commands.run("macos-version", ["sw_vers", "-productVersion"], timeout=30).read_text().strip()
    xcode = commands.run("xcode-version", ["xcodebuild", "-version"], timeout=30).read_text().strip()
    if macos.split(".")[0] != base["versions"]["macos"].split(".")[0] or xcode.splitlines()[0] != "Xcode " + base["versions"]["xcode"]:
        raise package.PackageError("Runner macOS/Xcode differs from the package pin")
    return {"macos": macos, "architecture": platform.machine(), "xcode": xcode,
            "runner_image": os.environ.get("ImageVersion")}


def verify_local_dependencies(checkout, base):
    # Both manifests are tracked by the pinned upstream commit; neither is an
    # overlay modification. The inspected graph has one local edge and no URLs.
    manifests = (EDITOR + "/Package.swift", LOCAL_DEPENDENCY + "/Package.swift")
    hashes = {}
    for relative in manifests:
        contents = package.bounded(checkout, relative).read_bytes()
        original = package.git(checkout, "show", base["commit"] + ":" + relative)
        if contents != original:
            raise package.PackageError("SwiftPM manifest differs from the pinned dependency graph")
        hashes[relative] = package.digest(contents)
    editor = package.bounded(checkout, manifests[0]).read_text(encoding="utf-8")
    mosaic = package.bounded(checkout, manifests[1]).read_text(encoding="utf-8")
    if (re.findall(r'\.package\(\s*path:\s*"([^"]+)"\s*\)', editor) != ["../../../MosaicLayout"]
            or len(re.findall(r"\.package\s*\(", editor)) != 1 or re.search(r"\.package\s*\(", mosaic)):
        raise package.PackageError("The reviewed SwiftPM local dependency closure changed")
    for relative in (EDITOR + "/Sources", EDITOR + "/Tests", LOCAL_DEPENDENCY + "/Sources"):
        if not package.bounded(checkout, relative).is_dir():
            raise package.PackageError("A pinned local SwiftPM source directory is missing")
    return {"manifest_sha256": hashes, "remote_dependencies": [], "submodules_required": False}


def verify_three_cases(cases, root, raw_log):
    if not isinstance(cases, list) or len(cases) != 3:
        raise package.PackageError("Native result must contain exactly the three requested UIKit cases")
    seen = set()
    for case in cases:
        match = re.search(r"(?:^|/)(?:RichTextEditorUIKitTests\.)?DocumentTextSelectionTests/([A-Za-z0-9_]+)(?:\(\))?$",
                          case.get("identifier", "")) if isinstance(case, dict) else None
        if not match or case.get("status") != "Success" or match[1] in seen:
            raise package.PackageError("Native UIKit result has a wrong, duplicate, skipped or failed case")
        seen.add(match[1])
    if seen != EXPECTED_METHODS:
        raise package.PackageError("Native UIKit result does not match the requested test names")
    # A resumed test process can leave plausible success counts. Preserve and
    # reject native issues and the upstream-documented crash/restart indicators.
    def no_failure_issues(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("errorSummaries", "testFailureSummaries") and isinstance(value, dict) and value.get("_values"):
                    raise package.PackageError("Native xcresult contains errors or test failures")
                no_failure_issues(value)
        elif isinstance(node, list):
            for value in node:
                no_failure_issues(value)
    no_failure_issues(root)
    with raw_log.open(encoding="utf-8", errors="replace") as log:
        if any(CRASH.search(line) for line in log):
            raise package.PackageError("Native test log contains a crash, trap or unexpected restart")
    return cases


def native_evidence(bundle, artifacts, raw_log):
    if not bundle.is_dir() or bundle.is_symlink():
        raise package.PackageError("Native XCTest did not produce its fresh xcresult bundle")
    references = []

    def read(reference=None):
        if reference is not None:
            reference = _schema.native_reference(reference)
            if len(references) >= 8 or reference in references:
                raise package.PackageError("Native XCTest result references are ambiguous or excessive")
            references.append(reference)
        filename = "xcresult-root" if reference is None else f"xcresult-tests-{len(references):02d}"
        command = ["xcrun", "xcresulttool", "get", "--legacy", "--format", "json", "--path", str(bundle)]
        if reference is not None:
            command.extend(["--id", reference])
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
        (artifacts / (filename + ".stderr.log")).write_bytes(result.stderr)
        if len(result.stdout) > 20 * 1024 * 1024:
            raise package.PackageError("Native XCTest result JSON is unexpectedly large")
        (artifacts / (filename + ".json")).write_bytes(result.stdout)
        if result.returncode:
            raise package.PackageError("xcresulttool could not read the native result")
        return json.loads(result.stdout)

    root = read()
    cases = _schema.xcresult_cases(root, read)
    proof = {"schema_version": 1, "test_filter": FILTER, "cases": cases, "native_references": references,
             "verified_utc": now(), "status": "unverified"}
    try:
        verify_three_cases(cases, root, raw_log)
        proof["status"] = "passed"
    finally:
        (artifacts / "native-cases.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    return proof


def execute_tests(checkout, work, artifacts, simulator_config, commands):
    session = SimulatorSession(artifacts / "simulator", label="editor", boot_timeout=300, **simulator_config)
    result_bundle = work / "selection-tests.xcresult"
    errors, proof = [], None
    try:
        session.create()
        common = ["xcodebuild", "-scheme", "RichTextEditor-Package", "-configuration", "Debug",
                  "-destination", "platform=iOS Simulator,id=" + session.udid,
                  "-derivedDataPath", work / "DerivedData", "-parallel-testing-enabled", "NO",
                  "-collect-test-diagnostics", "never", "-only-testing:" + FILTER]
        # Supported upstream SwiftPM workflow, split using Apple's documented
        # build-for-testing/test-without-building actions. No Bazel is involved.
        commands.run("build-for-testing", common + ["build-for-testing"],
                     cwd=checkout / EDITOR, timeout=1200)
        session.boot()
        try:
            commands.run("test-without-building", common + ["test-without-building", "-resultBundlePath", result_bundle],
                         cwd=checkout / EDITOR, timeout=300)
        except (package.PackageError, OSError) as error:
            errors.append(str(error))
        if result_bundle.exists():
            try:
                proof = native_evidence(result_bundle, artifacts, artifacts / "test-without-building.log")
            except (package.PackageError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
                errors.append("native evidence: " + str(error))
        else:
            errors.append("Native XCTest result bundle is missing")
    except (package.PackageError, OSError, ValueError) as error:
        errors.append(str(error))
    finally:
        try:
            session.cleanup()
        except (package.PackageError, OSError) as error:
            errors.append("owned Simulator cleanup: " + str(error))
    if errors:
        raise package.PackageError("; ".join(errors))
    if not proof or proof.get("status") != "passed":
        raise package.PackageError("No exact native UIKit pass evidence")
    return {"udid": session.udid, "runtime": session.runtime, "native_cases": proof}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args(argv)
    status, artifacts = {"schema_version": 1, "started_utc": now(), "status": "running"}, None
    try:
        temporary, artifacts = hosted_paths(args.artifacts)
        artifacts.mkdir(parents=True, exist_ok=False)
        (artifacts / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
        commands = Commands(artifacts)
        base, manifest = package.validate_package(ROOT)
        status.update(feature_commit=os.environ.get("DRAFT_POCKET_FEATURE_COMMIT"),
                      tested_merge_commit=os.environ.get("GITHUB_SHA"), upstream_commit=base["commit"],
                      feature_patch_sha256=manifest["feature"]["patch"]["sha256"], test_filter=FILTER,
                      expected_methods=sorted(EXPECTED_METHODS))
        status["toolchain"] = pinned_toolchain(base, commands)
        (artifacts / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
        checkout, work = temporary / "draft-pocket-editor-telegram-ios", temporary / "draft-pocket-editor-work"
        work.mkdir(exist_ok=False)
        if artifacts == checkout or artifacts.is_relative_to(checkout) or artifacts == work or artifacts.is_relative_to(work):
            raise package.PackageError("Uploaded evidence must be separate from checkout and native products")
        commands.run("bootstrap", [sys.executable, ROOT / "scripts/package.py", "bootstrap", checkout], timeout=300)
        commands.run("reconstruction", [sys.executable, ROOT / "scripts/package.py", "check", checkout], timeout=120)
        status["dependencies"] = verify_local_dependencies(checkout, base)
        (artifacts / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
        status["simulator"] = execute_tests(checkout, work, artifacts,
            package.native_simulator_spec(base["native_test_simulator"]), commands)
        status["status"] = "passed"
        return 0
    except (package.PackageError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        status.update(status="failed", error=str(error))
        print("error: " + str(error), file=sys.stderr)
        return 1
    finally:
        status["finished_utc"] = now()
        if artifacts is not None and artifacts.is_dir():
            (artifacts / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
