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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    status = {"schema_version": 1, "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "feature_commit": os.environ.get("DRAFT_POCKET_FEATURE_COMMIT"),
              "tested_merge_commit": os.environ.get("GITHUB_SHA"), "upstream_commit": None,
              "stages": {name: "not_run" for name in ("package_integrity", "toolchain", "configuration", "reconstruction", "build", "native_tests", "capture")}}
    redactions = []

    def save():
        (args.artifacts / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

    def redact(text):
        for value in redactions:
            text = text.replace(value, "[REDACTED]")
        return text

    def invoke(name, command, cwd=None):
        with (args.artifacts / f"{name}.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([str(v) for v in command], cwd=cwd, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            for line in process.stdout:
                clean = redact(line)
                log.write(clean)
                log.flush()
                print(clean, end="", flush=True)
            code = process.wait()
        if code:
            raise package.PackageError(f"{name} exited {code}; see its redacted log")

    stage = "package_integrity"
    status["stages"][stage] = "running"
    save()
    try:
        base, manifest = package.validate_package(ROOT)
        status["upstream_commit"] = base["commit"]
        status["patch_sha256"] = manifest["feature"].get("patch", {}).get("sha256")
        status["stages"][stage] = "passed"
        stage = "toolchain"
        status["stages"][stage] = "running"
        save()
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise package.PackageError("The pinned simulator build requires an Apple Silicon macOS runner")
        macos = package.run(["sw_vers", "-productVersion"]).decode().strip()
        xcode_path = Path("/Applications") / f"Xcode_{base['versions']['xcode']}.app/Contents/Developer"
        if not xcode_path.is_dir():
            raise package.PackageError(f"Pinned Xcode {base['versions']['xcode']} is not installed; no override/downgrade attempted")
        os.environ["DEVELOPER_DIR"] = str(xcode_path)
        xcode = package.run(["xcodebuild", "-version"]).decode().strip()
        if xcode.splitlines()[0] != "Xcode " + base["versions"]["xcode"]:
            raise package.PackageError("Installed Xcode differs from the locked version")
        if macos.split(".")[0] != base["versions"]["macos"].split(".")[0]:
            raise package.PackageError("Installed macOS major differs from the pinned toolchain")
        temporary = Path(os.environ.get("RUNNER_TEMP", args.artifacts.parent))
        runtime_info = json.loads(package.run(["xcrun", "simctl", "list", "runtimes", "--json"]))
        available_ios = [v for v in runtime_info["runtimes"] if v.get("isAvailable") and v.get("name", "").startswith("iOS ")]
        if not available_ios:
            raise package.PackageError("No available iOS Simulator runtime on this runner")
        evidence = {"macos": macos, "architecture": platform.machine(), "xcode": xcode,
                    "runner_image": os.environ.get("ImageVersion"),
                    "free_disk_bytes": shutil.disk_usage(temporary).free,
                    "ios_runtimes": [{"name": r["name"], "identifier": r["identifier"]} for r in available_ios]}
        (args.artifacts / "toolchain.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        status["stages"][stage] = "passed"
        stage = "configuration"
        status["stages"][stage] = "running"
        save()
        raw_config = os.environ.pop("DRAFT_POCKET_BUILD_CONFIGURATION_JSON", "")
        if not raw_config:
            raise package.PackageError("Missing existing DRAFT_POCKET_BUILD_CONFIGURATION_JSON secret; no credentials or repository settings were created")
        redactions.append(raw_config)
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
        invoke("reconstruction", [sys.executable, ROOT / "scripts/package.py", "check", checkout])
        status["stages"][stage] = "passed"
        stage = "build"
        status["stages"][stage] = "running"
        save()
        build_number = str(int(os.environ.get("GITHUB_RUN_NUMBER", "1")) * 100 + int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")))
        invoke("build", [sys.executable, ROOT / "scripts/package.py", "build", checkout,
                         "--configuration", configuration, "--cache", temporary / "draft-pocket-bazel-cache",
                         "--build-number", build_number])
        status["stages"][stage] = "passed"
        stage = "native_tests"
        status["stages"][stage] = "running"
        save()
        invoke("native-tests", [sys.executable, ROOT / "scripts/package.py", "test", "--native",
                                "--checkout", checkout, "--configuration", configuration,
                                "--cache", temporary / "draft-pocket-bazel-cache"])
        status["stages"][stage] = "passed"
        stage = "capture"
        status["stages"][stage] = "running"
        save()
        # There is deliberately no alternate screen, synthetic video, or real-account
        # login here. A full-client offline bootstrap is required before recording.
        if not manifest.get("demo", {}).get("available"):
            raise package.PackageError("Real demo capture is blocked: the package has no isolated full-client offline fixture. No capture was produced and no account was opened")
        raise package.PackageError("This package has no validated UI automation capture entry point; refusing to label an unobserved launch as a demo")
    except (package.PackageError, OSError, ValueError, KeyError) as error:
        status["stages"][stage] = "failed"
        status["failure"] = redact(str(error))
        print("error: " + status["failure"], file=sys.stderr)
        (args.artifacts / "failure.txt").write_text(status["failure"] + "\n", encoding="utf-8")
        return 1
    finally:
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
