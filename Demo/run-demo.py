#!/usr/bin/env python3
"""Launch the debug fixture on a new or recorded dedicated Draft Pocket Simulator."""

import argparse
import json
from pathlib import Path
import platform
import plistlib
import re
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import package


def contains_marker(path):
    marker = b"DRAFT_POCKET_ISOLATED_DEMO_V1"
    tail = b""
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            if marker in tail + chunk:
                return True
            tail = chunk[-len(marker):]
    return False


def owned_device(identity, devices, udid, runtime, bundle_id):
    if (identity.get("udid") != udid or identity.get("runtime") != runtime
            or identity.get("bundle_id") != bundle_id
            or not identity.get("name", "").startswith("Draft Pocket isolated ")):
        raise package.PackageError("Saved Simulator identity does not match this demo request")
    matches = [d for d in devices.get(runtime, []) if d.get("udid") == udid]
    if len(matches) != 1 or matches[0].get("name") != identity["name"] or not matches[0].get("isAvailable"):
        raise package.PackageError("Requested UDID is not the available dedicated Simulator recorded by this launcher")
    return matches[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True, help="Freshly built demo-enabled debug Simulator Telegram.app")
    parser.add_argument("--udid", type=lambda value: str(uuid.UUID(value)).upper(), help="Reuse only a dedicated Simulator previously created and recorded by this launcher")
    parser.add_argument("--namespace", default="manual-v1", help="New fixture namespace; no existing data is erased")
    args = parser.parse_args()
    base, manifest = package.validate_package(package.ROOT, with_demo=True)
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise package.PackageError("The pinned demo requires an Apple Silicon Mac")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.namespace):
        raise package.PackageError("Namespace must contain 1–64 ASCII letters, digits, underscores, or hyphens")
    app = args.app.resolve(strict=True)
    with (app / "Info.plist").open("rb") as source:
        info = plistlib.load(source)
    if "iPhoneSimulator" not in info.get("CFBundleSupportedPlatforms", []):
        raise package.PackageError("Only an iPhoneSimulator app bundle is accepted")
    executable = package.bounded(app, info["CFBundleExecutable"])
    if package.run(["xcodebuild", "-version"]).decode().splitlines()[0] != "Xcode " + base["versions"]["xcode"]:
        raise package.PackageError("Select the pinned Xcode version before launching")
    if "IOSSIMULATOR" not in package.run(["xcrun", "vtool", "-show-build", executable]).decode():
        raise package.PackageError("The app executable is not a Mach-O iOS Simulator binary")
    candidates = [executable]
    frameworks = package.bounded(app, "Frameworks")
    if frameworks.is_dir():
        for framework in frameworks.glob("*.framework"):
            relative_framework = framework.relative_to(app).as_posix()
            metadata = package.bounded(app, relative_framework + "/Info.plist")
            if metadata.is_file():
                with metadata.open("rb") as source:
                    framework_info = plistlib.load(source)
                candidates.append(package.bounded(app, relative_framework + "/" + framework_info["CFBundleExecutable"]))
    # AppDelegate lives in TelegramUIFramework at this base. Inspect executable
    # Mach-O code only; a marker copied into a resource is not sufficient.
    if not any(contains_marker(candidate) and "IOSSIMULATOR" in
               package.run(["xcrun", "vtool", "-show-build", candidate]).decode() for candidate in candidates):
        raise package.PackageError("App lacks the compiled debug Simulator fixture marker; no app was installed or launched")
    demo = manifest["demo"]
    runtimes = json.loads(package.run(["xcrun", "simctl", "list", "runtimes", "--json"]))["runtimes"]
    runtime = next((r for r in runtimes if r.get("isAvailable") and r.get("name", "").startswith("iOS ")
                    and r.get("version") == demo["os_version"]), None)
    devices = json.loads(package.run(["xcrun", "simctl", "list", "devicetypes", "--json"]))["devicetypes"]
    device = next((d for d in devices if d["name"] == demo["device_type"]), None)
    if not runtime or not device:
        raise package.PackageError("The exact demo Simulator device/runtime is unavailable")
    if args.udid:
        udid = args.udid
        identity_file = package.bounded(package.ROOT, f".local/demo-simulators/{udid}.json")
        identity = package.load_json(identity_file)
        available = json.loads(package.run(["xcrun", "simctl", "list", "devices", "--json"]))["devices"]
        existing = owned_device(identity, available, udid, runtime["identifier"], info["CFBundleIdentifier"])
        booted = existing.get("state") == "Booted"
    else:
        name = "Draft Pocket isolated " + str(uuid.uuid4())
        udid = package.run(["xcrun", "simctl", "create", name, device["identifier"], runtime["identifier"]]).decode().strip().upper()
        uuid.UUID(udid)
        identity = {"schema_version": 1, "udid": udid, "name": name, "runtime": runtime["identifier"],
                    "bundle_id": info["CFBundleIdentifier"], "device_type": device["identifier"]}
        identity_file = package.bounded(package.ROOT, f".local/demo-simulators/{udid}.json")
        identity_file.parent.mkdir(parents=True, exist_ok=True)
        with identity_file.open("x", encoding="utf-8") as output:
            json.dump(identity, output, indent=2)
        booted = False
    if not booted:
        package.run(["xcrun", "simctl", "boot", udid])
    package.run(["xcrun", "simctl", "bootstatus", udid, "-b"])
    package.run(["xcrun", "simctl", "install", udid, app])
    package.run(["xcrun", "simctl", "launch", udid, info["CFBundleIdentifier"], "--draft-pocket-demo",
                 "--draft-pocket-demo-data", args.namespace, "-AppleLanguages", "(en)", "-AppleLocale", "en_US"])
    package.run(["open", "-a", "Simulator", "--args", "-CurrentDeviceUDID", udid])
    print(f"Launch requested on recorded dedicated Simulator {udid}. Inspect the real UI; this is not a runtime test result.")


if __name__ == "__main__":
    try:
        main()
    except (package.PackageError, OSError, ValueError, KeyError) as error:
        print("error: " + str(error), file=sys.stderr)
        sys.exit(1)
