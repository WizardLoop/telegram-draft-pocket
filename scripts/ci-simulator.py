#!/usr/bin/env python3
"""Manual hosted-runner attempt. Writes evidence of actual stages, including failures."""

import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import package
from disk_guard import DiskGuard, DiskGuardError
from simulator import select_simulator


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args(argv)
    args.artifacts.mkdir(parents=True, exist_ok=True)
    status = {"schema_version": 1, "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "feature_commit": os.environ.get("DRAFT_POCKET_FEATURE_COMMIT"),
              "tested_merge_commit": os.environ.get("GITHUB_SHA"), "upstream_commit": None,
              "stages": {name: "not_run" for name in ("package_integrity", "portable_tests", "toolchain", "configuration", "reconstruction", "build", "capture", "native_tests")}}
    raw_config = os.environ.pop("DRAFT_POCKET_BUILD_CONFIGURATION_JSON", "")
    redactions = [raw_config] if raw_config else []
    last_stage = None
    configuration = None
    observed_states = {}

    def save():
        nonlocal last_stage
        for name, result in status["stages"].items():
            if result != "not_run" and observed_states.get(name) != result:
                status.setdefault("stage_times_utc", {}).setdefault(name, {})[result] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                observed_states[name] = result
        (args.artifacts / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
        current = next((name for name, result in status["stages"].items() if result == "running"), None)
        if current and current != last_stage:
            print(f"Starting stage: {current}", flush=True)
            last_stage = current

    def redact(text):
        for value in redactions:
            text = text.replace(value, "[REDACTED]")
        return text

    def invoke(name, command, cwd=None):
        # The tiny read-only case review may use the reserved evidence space
        # after an expensive stage was stopped; it never starts a build/device.
        guard = DiskGuard(args.artifacts, name, **({"minimum_free_bytes": 1} if name == "native-case-review" else {}))
        process, failure = None, None
        guard_failures = []
        try:
            if guard:
                guard.preflight()
            with (args.artifacts / f"{name}.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen([str(v) for v in command], cwd=cwd, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", start_new_session=True)
                if guard:
                    guard.start(process)
                for line in process.stdout:
                    clean = redact(line)
                    log.write(clean)
                    log.flush()
                    print(clean, end="", flush=True)
                code = process.wait()
            if code:
                raise package.PackageError(f"{name} exited {code}; see its redacted log")
        except BaseException as error:
            failure = error
        finally:
            if guard:
                try:
                    guard.stop(terminate=process is not None and (failure is not None or process.poll() is None))
                except DiskGuardError as error:
                    guard_failures.append(str(error))
                try:
                    guard.raise_if_failed()
                except DiskGuardError as error:
                    guard_failures.append(str(error))
        if isinstance(failure, (KeyboardInterrupt, SystemExit)):
            for error in guard_failures:
                print(redact(error), file=sys.stderr, flush=True)
            raise failure
        if isinstance(failure, DiskGuardError) or guard_failures:
            messages = ([str(failure)] if failure is not None else []) + guard_failures
            raise package.PackageError("; ".join(dict.fromkeys(messages)))
        if failure is not None:
            raise failure

    def record_failure(name, error):
        message = redact(str(error))
        status["stages"][name] = "failed"
        status.setdefault("failures", {})[name] = message
        status["failure"] = "\n".join(f"{key}: {value}" for key, value in status["failures"].items())
        print(f"error ({name}): {message}", file=sys.stderr, flush=True)
        (args.artifacts / f"{name}-failure.txt").write_text(message + "\n", encoding="utf-8")
        (args.artifacts / "failure.txt").write_text(status["failure"] + "\n", encoding="utf-8")
        save()

    stage = "package_integrity"
    status["stages"][stage] = "running"
    save()
    try:
        base, manifest = package.validate_package(ROOT)
        with_demo = manifest.get("demo", {}).get("available", False)
        if with_demo:
            package.validate_package(ROOT, with_demo=True)
        demo_flags = ["--with-demo"] if with_demo else []
        status["upstream_commit"] = base["commit"]
        status["patch_sha256"] = manifest["feature"].get("patch", {}).get("sha256")
        status["demo_patch_sha256"] = manifest.get("demo", {}).get("patch", {}).get("sha256")
        status["stages"][stage] = "passed"
        stage = "portable_tests"
        status["stages"][stage] = "running"
        save()
        # Catch host-specific packaging failures before reconstruction or the full app build.
        # Keep the later native invocation unchanged so every existing validation still runs.
        invoke("portable-tests", [sys.executable, ROOT / "scripts/package.py", "test", *demo_flags])
        status["stages"][stage] = "passed"
        stage = "toolchain"
        status["stages"][stage] = "running"
        save()
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise package.PackageError("The pinned simulator build requires an Apple Silicon macOS runner")
        macos = package.run(["sw_vers", "-productVersion"], timeout=30).decode().strip()
        xcode_path = Path("/Applications") / f"Xcode_{base['versions']['xcode']}.app/Contents/Developer"
        if not xcode_path.is_dir():
            raise package.PackageError(f"Pinned Xcode {base['versions']['xcode']} is not installed; no override/downgrade attempted")
        os.environ["DEVELOPER_DIR"] = str(xcode_path)
        xcode = package.run(["xcodebuild", "-version"], timeout=30).decode().strip()
        if xcode.splitlines()[0] != "Xcode " + base["versions"]["xcode"]:
            raise package.PackageError("Installed Xcode differs from the locked version")
        if macos.split(".")[0] != base["versions"]["macos"].split(".")[0]:
            raise package.PackageError("Installed macOS major differs from the pinned toolchain")
        temporary = Path(os.environ.get("RUNNER_TEMP", args.artifacts.parent))
        simulator_preflight = {"status": "checking", "selections": []}
        preflight_path = args.artifacts / "simulator-preflight.json"
        try:
            runtime_info = json.loads(package.run(["xcrun", "simctl", "list", "runtimes", "--json"], timeout=30))
            device_info = json.loads(package.run(["xcrun", "simctl", "list", "devicetypes", "--json"], timeout=30))
            required = [("native", package.native_simulator_spec(base.get("native_test_simulator")))]
            if with_demo:
                required.append(("capture", package.native_simulator_spec(
                    {key: manifest["demo"].get(key) for key in ("device_type", "os_version")})))
            for lane, specification in required:
                runtime, device = select_simulator(runtime_info, device_info, **specification)
                simulator_preflight["selections"].append({"lane": lane, **specification,
                    "runtime_identifier": runtime["identifier"], "device_identifier": device["identifier"]})
            simulator_preflight["status"] = "passed"
        except (package.PackageError, OSError, ValueError, KeyError, TypeError) as error:
            simulator_preflight.update(status="failed", error=str(error))
            raise package.PackageError("Exact Simulator preflight failed: " + str(error)) from error
        finally:
            preflight_path.write_text(json.dumps(simulator_preflight, indent=2) + "\n", encoding="utf-8")
        if with_demo:
            if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
                # Runner inventory does not list FFmpeg. Install only in this
                # ephemeral standard runner, before the expensive native build.
                os.environ["HOMEBREW_NO_AUTO_UPDATE"] = "1"
                os.environ["HOMEBREW_NO_INSTALL_CLEANUP"] = "1"
                invoke("media-tools-install", ["brew", "install", "ffmpeg"])
            for required in ("ffmpeg", "ffprobe"):
                if not shutil.which(required):
                    raise package.PackageError(f"Required media inspection tool {required} is not installed; stopped before full build")
            (args.artifacts / "media-tools.txt").write_bytes(
                package.run(["ffmpeg", "-version"]) + package.run(["ffprobe", "-version"]))
        evidence = {"macos": macos, "architecture": platform.machine(), "xcode": xcode,
                    "runner_image": os.environ.get("ImageVersion"),
                    "free_disk_bytes": shutil.disk_usage(temporary).free,
                    "simulator_selections": simulator_preflight["selections"]}
        (args.artifacts / "toolchain.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        status["stages"][stage] = "passed"
        stage = "configuration"
        status["stages"][stage] = "running"
        save()
        if not raw_config:
            raise package.PackageError("Missing existing DRAFT_POCKET_BUILD_CONFIGURATION_JSON secret; no credentials or repository settings were created")
        try:
            config = json.loads(raw_config)
        except ValueError:
            raise package.PackageError("Build configuration secret is not valid JSON") from None
        package.validate_configuration(config)
        for key in ("api_id", "api_hash", "team_id", "app_center_id"):
            if key in config and len(str(config[key])) > 2:
                redactions.append(str(config[key]))
                # GitHub masks each field, not only the multi-line JSON secret.
                print("::add-mask::" + str(config[key]).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A"))
        configuration = temporary / "draft-pocket-build-configuration.json"
        configuration.write_text(json.dumps(config), encoding="utf-8")
        configuration.chmod(0o600)
        status["stages"][stage] = "passed"
        stage = "reconstruction"
        status["stages"][stage] = "running"
        save()
        checkout = temporary / "draft-pocket-telegram-ios"
        invoke("bootstrap", [sys.executable, ROOT / "scripts/package.py", "bootstrap", checkout, "--submodules"])
        invoke("reconstruction", [sys.executable, ROOT / "scripts/package.py", "check", checkout, *demo_flags])
        status["stages"][stage] = "passed"
        stage = "build"
        status["stages"][stage] = "running"
        save()
        build_number = str(int(os.environ.get("GITHUB_RUN_NUMBER", "1")) * 100 + int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")))
        invoke("build", [sys.executable, ROOT / "scripts/package.py", "build", checkout,
                         "--configuration", configuration, "--no-disk-cache",
                         "--build-number", build_number, *demo_flags])
        status["stages"][stage] = "passed"
        # Capture gets a clean Simulator lane and the available post-build time.
        # Both checks remain independent: a failed capture still runs native tests.
        stage = "capture"
        status["stages"][stage] = "running"
        save()
        try:
            if not manifest.get("demo", {}).get("available"):
                raise package.PackageError("Real demo capture is blocked: the package has no isolated full-client offline fixture. No capture was produced and no account was opened")
            invoke("capture", [sys.executable, ROOT / "scripts/capture-demo.py", "--checkout", checkout,
                               "--configuration", configuration, "--no-disk-cache",
                               "--artifacts", args.artifacts])
        except (package.PackageError, OSError, ValueError, KeyError) as error:
            record_failure(stage, error)
        else:
            status["stages"][stage] = "passed"
        stage = "native_tests"
        status["stages"][stage] = "running"
        save()
        native_artifacts = args.artifacts / "native-tests"
        native_artifacts.mkdir(parents=True, exist_ok=True)
        native_errors = []
        native_commands = [
            ("native-tests", [sys.executable, ROOT / "scripts/package.py", "test", "--native",
                "--checkout", checkout, "--configuration", configuration,
                "--no-disk-cache", "--artifacts", native_artifacts, *demo_flags]),
            ("native-case-review", [sys.executable, ROOT / "scripts/native_evidence.py", "--package", ROOT,
                "--log", args.artifacts / "native-tests.log",
                "--targets", native_artifacts / "native-target-results.json",
                "--output", native_artifacts / "native-case-review.json"]),
        ]
        # Preserve exact case evidence even after an unsuccessful target. A
        # successful Bazel exit cannot hide missing, skipped or retried cases.
        for name, command in native_commands:
            try:
                invoke(name, command)
            except (package.PackageError, OSError, ValueError, KeyError) as error:
                native_errors.append(str(error))
        if native_errors:
            record_failure(stage, package.PackageError("; ".join(native_errors)))
        else:
            status["stages"][stage] = "passed"
    except (package.PackageError, OSError, ValueError, KeyError) as error:
        record_failure(stage, error)
        return 1
    finally:
        status["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if configuration and configuration.is_file():
            configuration.unlink()
        save()
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as output:
                output.write("## Draft Pocket validation evidence\n\n")
                output.write("| Stage | Actual result |\n|---|---|\n")
                for name, result in status["stages"].items():
                    output.write(f"| {name} | {result} |\n")
                if status.get("failure"):
                    output.write("\n" + status["failure"].replace("\n", " ") + "\n")
    return 1 if status.get("failures") else 0


if __name__ == "__main__":
    sys.exit(main())
