"""Export only the isolated demo's enum-only launch report from its owned device."""

import hashlib
import json
import math
from pathlib import Path
import stat
import uuid

import package


REPORT_PATH = "Library/Caches/draft-pocket-demo-launch.json"
MAX_BYTES = 16 * 1024
# Kept identical to DraftPocketDemo.LaunchPhase in the separately gated fixture.
PHASES = frozenset({
    "launchEntered", "metalInitializing", "metalReady", "passcodeConfigured", "appGroupResolved", "appGroupMissing",
    "buildConfigStarted", "buildConfigReady", "isolatedRootSelected", "writeCheckPassed",
    "writeCheckFailed", "accountManagerReady", "sharedContextReady", "unauthorizedAccountReady",
    "seedStarted", "seedCommitted", "authorizedRecordCommitted", "authorizedContextCreated",
    "authorizedContextReady", "chatRouteRequested", "launchReturned",
})


def owned_data_container(path, simulator_home, udid):
    root = simulator_home.resolve(strict=True)
    candidate = Path(path)
    canonical_udid = str(uuid.UUID(udid)).upper()
    if not candidate.is_absolute():
        raise package.PackageError("Simulator returned a relative data container")
    try:
        relative = Path(*candidate.parts[-6:])
        if (candidate.parents[5].resolve(strict=True) != root
                or relative.parts[:5] != (canonical_udid, "data", "Containers", "Data", "Application")):
            raise ValueError("Unexpected data container")
        uuid.UUID(relative.parts[5])
    except (OSError, IndexError, ValueError):
        raise package.PackageError("Data container is outside the owned Simulator") from None
    container = package.bounded(root, relative.as_posix())
    if not container.is_dir():
        raise package.PackageError("Owned data container does not exist")
    return container


def validate_report(content):
    if not content or len(content) > MAX_BYTES:
        raise package.PackageError("Demo launch report exceeds its bounded schema")

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate key")
            result[key] = value
        return result

    try:
        report = json.loads(content, object_pairs_hook=object_pairs)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise package.PackageError("Invalid demo launch report JSON") from None
    if (not isinstance(report, dict) or set(report) != {"schema_version", "phases"}
            or type(report["schema_version"]) is not int or report["schema_version"] != 1
            or not isinstance(report["phases"], list) or not 1 <= len(report["phases"]) <= 64):
        raise package.PackageError("Invalid demo launch report schema")
    for event in report["phases"]:
        if (not isinstance(event, dict) or set(event) != {"phase", "utc"}
                or not isinstance(event["phase"], str) or event["phase"] not in PHASES
                or type(event["utc"]) not in (int, float)
                or not 0 <= event["utc"] < 253402300800 or not math.isfinite(event["utc"])):
            raise package.PackageError("Invalid demo launch phase")
    return report


def export_launch_report(udid, bundle_id, simulator_home, artifacts):
    path = package.run(["xcrun", "simctl", "get_app_container", udid, bundle_id, "data"],
                       timeout=15).decode("utf-8").strip()
    container = owned_data_container(path, simulator_home, udid)
    source = package.bounded(container, REPORT_PATH)
    if not source.exists():
        return {"status": "absent", "note": "No demo launch phase file was produced."}
    if not stat.S_ISREG(source.stat().st_mode) or source.stat().st_size > MAX_BYTES:
        raise package.PackageError("Demo launch report is not a bounded regular file")
    # Read a bounded amount even if the source grows after stat.
    with source.open("rb") as stream:
        content = stream.read(MAX_BYTES + 1)
    report = validate_report(content)
    destination = artifacts / "demo-launch-phases.json"
    with destination.open("xb") as output:
        output.write(content)
    return {"status": "saved", "path": destination.name, "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(), "count": len(report["phases"])}
