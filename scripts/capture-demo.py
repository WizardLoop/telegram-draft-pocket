#!/usr/bin/env python3
"""Record a real full-client XCTest run on one new, explicitly addressed Simulator."""

import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile

import package


def xcresult_cases(root, read_reference):
    """Read the legacy native schema used by the pinned xctestrunner itself."""
    cases = []

    def values(container, key):
        wrapper = container.get(key) if isinstance(container, dict) else None
        result = wrapper.get("_values") if isinstance(wrapper, dict) else None
        if not isinstance(result, list):
            raise package.PackageError(f"Native xcresult has an unsupported {key} schema")
        return result

    def value(container, key):
        wrapper = container.get(key) if isinstance(container, dict) else None
        return wrapper.get("_value") if isinstance(wrapper, dict) else None

    def visit(node, ancestors):
        if not isinstance(node, dict):
            raise package.PackageError("Native xcresult contains an invalid test node")
        identifier = value(node, "identifier") or value(node, "name")
        names = ancestors + ([identifier] if isinstance(identifier, str) else [])
        if "subtests" in node:
            for child in values(node, "subtests"):
                visit(child, names)
        else:
            status = value(node, "testStatus")
            if not isinstance(identifier, str) or not isinstance(status, str):
                raise package.PackageError("Native xcresult leaf lacks test identity/status")
            cases.append({"identifier": "/".join(names), "status": status})

    actions = values(root, "actions")
    references = []
    for action in actions:
        if not isinstance(action, dict) or not isinstance(action.get("_type"), dict):
            raise package.PackageError("Native xcresult contains an invalid action")
        if action["_type"].get("_name") != "ActionRecord":
            continue
        action_result = action.get("actionResult")
        reference = action_result.get("testsRef") if isinstance(action_result, dict) else None
        identifier = value(reference, "id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_~+=/.-]{0,2047}", identifier):
            raise package.PackageError("Native xcresult has no valid test-results reference")
        references.append(identifier)
    if not references or len(set(references)) != len(references):
        raise package.PackageError("Native xcresult test references are empty or ambiguous")
    for reference in references:
        plans = read_reference(reference)
        for plan in values(plans, "summaries"):
            for testable in values(plan, "testableSummaries"):
                for test in values(testable, "tests"):
                    visit(test, [])
    return cases


def copy_xcresult(outputs, destination):
    """Copy only the native result bundle; reject archive traversal/links first."""
    bundle = outputs / "test.xcresult"
    archive = outputs / "outputs.zip"
    if bundle.exists() and archive.exists():
        raise package.PackageError("Ambiguous native result: both directory and archive exist")
    target = destination / "test.xcresult"
    if target.exists():
        raise package.PackageError("Native result extraction destination already exists")
    if bundle.is_dir():
        if bundle.is_symlink() or getattr(bundle, "is_junction", lambda: False)():
            raise package.PackageError("Native result bundle must not be a link")
        sources = []
        for source in bundle.rglob("*"):
            if source.is_symlink() or getattr(source, "is_junction", lambda: False)():
                raise package.PackageError("Native result contains a link")
            relative = package.relative(source.relative_to(bundle).as_posix())
            if source.is_file():
                sources.append((source, relative))
            elif not source.is_dir():
                raise package.PackageError("Native result contains a non-regular file")
        if sum(source.stat().st_size for source, _ in sources) > 1024 ** 3:
            raise package.PackageError("Native result bundle exceeds the 1 GiB copy limit")
        for source, relative in sources:
            output = package.bounded(destination, "test.xcresult/" + relative)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, output)
    elif archive.is_file():
        if archive.is_symlink():
            raise package.PackageError("Native result archive must not be a link")
        with zipfile.ZipFile(archive) as zipped:
            selected, seen, size = [], set(), 0
            for entry in zipped.infolist():
                name = package.relative(entry.filename.rstrip("/"))
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or entry.flag_bits & 1:
                    raise package.PackageError("Native result archive contains a link, special or encrypted entry")
                if name.casefold() in seen:
                    raise package.PackageError("Native result archive has duplicate/case-colliding paths")
                seen.add(name.casefold())
                if name.startswith("test.xcresult/") and not entry.is_dir():
                    size += entry.file_size
                    selected.append((entry, name))
            if not selected or size > 1024 ** 3:
                raise package.PackageError("Native result archive is missing its bundle or exceeds the 1 GiB extraction limit")
            for entry, name in selected:
                output = package.bounded(destination, name)
                output.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(entry) as source, output.open("xb") as sink:
                    shutil.copyfileobj(source, sink)
    else:
        raise package.PackageError("Missing native test.xcresult in Bazel undeclared test outputs")
    if not target.is_dir() or not any(p.is_file() for p in target.rglob("*")):
        raise package.PackageError("Native result bundle is empty")
    return target


def native_results(test_directory, artifacts, redact):
    with tempfile.TemporaryDirectory(prefix="draft-pocket-xcresult-") as temporary:
        bundle = copy_xcresult(test_directory / "test.outputs", Path(temporary))
        hashes = []
        for source in sorted(p for p in bundle.rglob("*") if p.is_file()):
            checksum = hashlib.sha256()
            with source.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    checksum.update(chunk)
            hashes.append({"path": source.relative_to(bundle).as_posix(), "sha256": checksum.hexdigest(), "bytes": source.stat().st_size})
        (artifacts / "native-xcresult-files.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
        # This exact legacy command/schema is also used by pinned xctestrunner
        # 4c6f4931aa65b3e6573b3ca11753c98fc8d3071c on Xcode >=16.
        command = ["xcrun", "xcresulttool", "get", "--format", "json", "--path", bundle, "--legacy"]
        root_text = package.run(command).decode("utf-8")
        (artifacts / "native-xcresult-root.json").write_text(redact(root_text), encoding="utf-8")
        references = []

        def read_reference(identifier):
            data = package.run(command + ["--id", identifier]).decode("utf-8")
            (artifacts / f"native-xcresult-tests-{len(references):02d}.json").write_text(redact(data), encoding="utf-8")
            references.append(identifier)
            return json.loads(data)

        cases = xcresult_cases(json.loads(root_text), read_reference)
        (artifacts / "native-xctest-cases.json").write_text(redact(json.dumps(cases, indent=2)) + "\n", encoding="utf-8")
        return cases


def verify_scenario(native_cases, events, test_filter, started, finished):
    """A generic passing test and an animated screen are not scenario evidence."""
    if not isinstance(native_cases, list) or any(not isinstance(case, dict) or not isinstance(case.get("identifier"), str)
                                              or not isinstance(case.get("status"), str) for case in native_cases):
        raise package.PackageError("Scenario proof requires native xcresult test cases, not generic XML")
    cases = [case for case in native_cases if case.get("status") != "Skipped"]
    expected_class, expected_method = test_filter.split("/", 1)
    identity = cases[0].get("identifier", "") if len(cases) == 1 else ""
    exact_name = lambda name: re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", identity)
    if (len(cases) != 1 or not exact_name(expected_class) or not exact_name(expected_method)
            or cases[0].get("status") != "Success"):
        raise package.PackageError("Native xcresult does not prove the single requested scenario passed")
    if expected_method == "testCaptureProductionStory":
        required = ["story_start", "put_aside_start", "put_aside_end", "put_aside_start", "put_aside_end",
                    "send_local_start", "send_local_end", "restore_start", "restore_end",
                    "send_local_start", "send_local_end", "story_end"]
    elif expected_method == "testQASwapDeleteUndoRestartAndChatIsolation":
        required = ["qa_start", "put_aside_start", "put_aside_end", "restore_start", "restore_end",
                    "delete_start", "delete_end", "undo_start", "undo_end", "qa_end"]
    else:
        raise package.PackageError("Unknown demo scenario")
    position = 0
    previous = started - 1.0
    for event in events:
        instant = event.get("utc")
        if (isinstance(instant, bool) or not isinstance(instant, (int, float)) or not math.isfinite(instant)
                or not started - 1.0 <= instant <= finished + 1.0 or instant < previous):
            raise package.PackageError("Scenario marker timestamps are invalid or outside the continuous take")
        previous = instant
        if position < len(required) and event.get("event") == required[position]:
            position += 1
    if position != len(required):
        raise package.PackageError("Required real UI action markers are missing or out of order")
    return len(cases)


def inspect_media(raw, artifacts):
    """Report encoded timestamps AND decoded motion; neither implies 60 fps UI."""
    media = package.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", raw])
    (artifacts / "capture-format.json").write_bytes(media)
    if any(s.get("codec_type") == "audio" for s in json.loads(media).get("streams", [])):
        raise package.PackageError("Unexpected audio stream in the raw Simulator recording")
    probe = package.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams",
                         "-show_frames", "-show_entries", "frame=best_effort_timestamp_time,pkt_duration_time:stream",
                         "-of", "json", raw])
    (artifacts / "capture-ffprobe.json").write_bytes(probe)
    info = json.loads(probe)
    frames = info.get("frames", [])
    pts = [float(f["best_effort_timestamp_time"]) for f in frames if "best_effort_timestamp_time" in f]
    if len(pts) < 2 or pts[-1] <= pts[0] or any(b < a for a, b in zip(pts, pts[1:])):
        raise package.PackageError("Recording has too few frames or invalid presentation timestamps")
    stream = info["streams"][0]
    width = 160
    height = max(1, round(int(stream["height"]) * width / int(stream["width"])))
    frame_size = width * height
    command = ["ffmpeg", "-v", "error", "-i", str(raw), "-vf", f"scale={width}:{height}",
               "-fps_mode", "passthrough", "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"]
    motion = []
    with (artifacts / "capture-decode.log").open("wb") as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log)
        previous = None
        count = 0
        while True:
            data = process.stdout.read(frame_size)
            if not data:
                break
            if len(data) != frame_size:
                process.kill()
                raise package.PackageError("Incomplete decoded video frame")
            if previous is not None and count < len(pts):
                difference = sum(abs(a - b) for a, b in zip(data, previous)) / frame_size
                if difference >= 1.0:
                    motion.append({"frame": count, "pts": pts[count],
                                   "delta_seconds": pts[count] - pts[count - 1],
                                   "mean_pixel_change": round(difference, 4)})
            previous = data
            count += 1
        if process.wait():
            raise package.PackageError("Could not decode the recording; inspect capture-decode.log")
    deltas = [b - a for a, b in zip(pts, pts[1:])]
    moving_deltas = [m["delta_seconds"] for m in motion]
    with raw.open("rb") as source:
        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
        raw_hash = digest.hexdigest()
    summary = {
        "sha256": raw_hash,
        "duration_seconds": pts[-1] - pts[0], "decoded_frames": count,
        "width": stream["width"], "height": stream["height"],
        "container_average_rate": stream.get("avg_frame_rate"),
        "median_frame_interval_seconds": statistics.median(deltas),
        "max_frame_interval_seconds": max(deltas),
        "duplicate_presentation_timestamps": sum(delta == 0 for delta in deltas),
        "visually_changed_adjacent_frames": len(motion),
        "motion_interval_median_seconds": statistics.median(moving_deltas) if moving_deltas else None,
        "motion_interval_max_seconds": max(moving_deltas) if moving_deltas else None,
        "method": "Native recording PTS plus mean absolute luma difference on every decoded 160px-wide frame; threshold 1/255.",
        "interpretation": "Capture cadence and pixel changes are evidence only. A 60 fps container does not prove 60 distinct UI updates or smooth animation. Review motion frames and raw take.",
        "motion_frames": motion,
    }
    events_path = artifacts / "action-timeline.json"
    identity = json.loads((artifacts / "capture-identity.json").read_text(encoding="utf-8"))
    origin = datetime.datetime.fromisoformat(identity["recording_ready_utc"]).timestamp()
    events = json.loads(events_path.read_text(encoding="utf-8")) if events_path.is_file() else []
    windows = []
    for index, event in enumerate(events):
        name = event.get("event", "")
        if name not in ("put_aside_start", "restore_start", "delete_start", "undo_start"):
            continue
        ending = next((e for e in events[index + 1:] if e.get("event") == name.replace("_start", "_end")), None)
        start = float(event["utc"]) - origin
        end = float(ending["utc"]) - origin + 0.3 if ending else start + 1.0
        changed = [m for m in motion if start <= m["pts"] <= end]
        window = {"event": name, "approximate_start_pts": start, "approximate_end_pts": end,
                  "changed_frames": len(changed), "alignment": "Test wall clock relative to recorder-ready acknowledgment; inspect raw take to confirm exact action boundary."}
        if changed:
            chosen = changed[len(changed) // 2]["frame"]
            window["inspected_frame"] = chosen
            window["max_changed_frame_interval_seconds"] = max(m["delta_seconds"] for m in changed)
            gaps = [b["pts"] - a["pts"] for a, b in zip(changed, changed[1:])]
            window["max_gap_between_visible_changes_seconds"] = max(gaps) if gaps else None
            window["frames_below_change_threshold"] = sum(start <= p <= end for p in pts) - len(changed)
            window["gap_caveat"] = "Includes legitimate pauses and sub-threshold motion; inspect adjacent native frames before classifying a stall."
            package.run(["ffmpeg", "-v", "error", "-i", raw, "-vf",
                         f"select=between(n\\,{max(0, chosen - 2)}\\,{chosen + 2})",
                         "-fps_mode", "passthrough", "-frames:v", "5", artifacts / f"action-{index:02d}-%02d.png"])
        windows.append(window)
    summary["action_motion_windows"] = windows
    (artifacts / "capture-analysis.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if count != len(pts) or len(motion) < 10:
        raise package.PackageError("Recording lacks sufficient decoded motion evidence for a demo")
    # Preserve actual neighboring frames during motion for visual inspection.
    chosen = motion[len(motion) // 2]["frame"]
    package.run(["ffmpeg", "-v", "error", "-i", raw, "-vf",
                 f"select=between(n\\,{max(0, chosen - 2)}\\,{chosen + 2})",
                 "-fps_mode", "passthrough", "-frames:v", "5", artifacts / "motion-%02d.png"])


def capture(checkout, configuration, cache, artifacts, test_filter):
    package.toolchain(package.ROOT, checkout, configuration, with_demo=True)
    _, manifest = package.validate_package(package.ROOT, with_demo=True)
    demo = manifest["demo"]
    for tool in ("ffmpeg", "ffprobe"):
        if not package.shutil.which(tool):
            raise package.PackageError(f"{tool} is required for actual video inspection")
    config = package.load_json(configuration)
    secrets = [str(config[k]) for k in ("api_id", "api_hash", "team_id", "app_center_id") if len(str(config[k])) > 2]

    def redact(text):
        for value in secrets:
            text = text.replace(value, "[REDACTED]")
        return text

    build_command = package.make_command(checkout, configuration, cache, "test") + [
        "--target", demo["native_test_target"], "--buildOnly"]
    print("Preparing the exact XCTest target before starting the continuous take.", flush=True)
    with (artifacts / "demo-test-build.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([str(x) for x in build_command], cwd=checkout, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        for line in process.stdout:
            clean = redact(line)
            log.write(clean)
            log.flush()
            print(clean, end="", flush=True)
        if process.wait():
            raise package.PackageError("Demo XCTest target failed to build; recording was not started")

    runtimes = json.loads(package.run(["xcrun", "simctl", "list", "runtimes", "--json"]))["runtimes"]
    runtime = next((r for r in runtimes if r.get("isAvailable") and r.get("version") == demo["os_version"]
                    and r.get("name", "").startswith("iOS ")), None)
    types = json.loads(package.run(["xcrun", "simctl", "list", "devicetypes", "--json"]))["devicetypes"]
    device = next((d for d in types if d["name"] == demo["device_type"]), None)
    if not runtime or not device:
        raise package.PackageError("The exact demo device/runtime is unavailable; no substitution attempted")
    udid = package.run(["xcrun", "simctl", "create", "Draft Pocket isolated " + str(uuid.uuid4()),
                        device["identifier"], runtime["identifier"]]).decode().strip()
    uuid.UUID(udid)
    identity = {"udid": udid, "device_type": demo["device_type"], "runtime": runtime["identifier"],
                "feature_commit": os.environ.get("DRAFT_POCKET_FEATURE_COMMIT"),
                "tested_merge_commit": os.environ.get("GITHUB_SHA"),
                "feature_patch_sha256": manifest["feature"]["patch"]["sha256"],
                "demo_patch_sha256": demo["patch"]["sha256"],
                "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "scenario": "Fictional offline @wizardloop fixture using the real Telegram client and Pocket implementation"}
    identity["test_filter"] = test_filter
    identity_path = artifacts / "capture-identity.json"
    identity_path.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
    recorder = None
    raw = artifacts / "draft-pocket-continuous-raw.mov"
    ready = threading.Event()
    error = None
    events = []
    try:
        package.run(["xcrun", "simctl", "boot", udid])
        package.run(["xcrun", "simctl", "bootstatus", udid, "-b"])
        package.run(["xcrun", "simctl", "status_bar", udid, "override", "--time", "9:41", "--batteryState", "charged", "--batteryLevel", "100"])
        recorder = subprocess.Popen(["xcrun", "simctl", "io", udid, "recordVideo", "--codec=h264", str(raw)],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")

        def record_log():
            with (artifacts / "capture-recorder.log").open("w", encoding="utf-8") as log:
                for line in recorder.stdout:
                    log.write(line)
                    log.flush()
                    if "Recording started" in line:
                        ready.set()

        reader = threading.Thread(target=record_log, daemon=True)
        reader.start()
        if not ready.wait(30) or recorder.poll() is not None:
            raise package.PackageError("Simulator recording did not report ready; XCTest was not started")
        identity["recording_ready_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        command = package.make_command(checkout, configuration, cache, "test") + [
            "--target", demo["native_test_target"], "--simulatorUDID", udid, "--testFilter", test_filter]
        with (artifacts / "demo-xctest.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([str(x) for x in command], cwd=checkout, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            for line in process.stdout:
                clean = redact(line)
                log.write(clean)
                log.flush()
                print(clean, end="", flush=True)
                if "DRAFT_POCKET_EVENT " in clean:
                    try:
                        events.append(json.loads(clean.split("DRAFT_POCKET_EVENT ", 1)[1]))
                    except ValueError:
                        pass
            code = process.wait()
        identity["xctest_exit_code"] = code
        if recorder.poll() is not None:
            raise package.PackageError("Recorder stopped before XCTest completed; take is incomplete")
        if code:
            raise package.PackageError(f"Real demo XCTest failed with exit {code}; raw evidence retained")
        test_directory = checkout / "bazel-testlogs/Tests/DraftPocketDemo/DraftPocketDemoUITests"
        report = test_directory / "test.xml"
        if report.is_file():
            (artifacts / "bazel-wrapper.xml").write_text(redact(report.read_text(encoding="utf-8")), encoding="utf-8")
        cases = native_results(test_directory, artifacts, redact)
        identity["reported_test_cases"] = verify_scenario(
            cases, events, test_filter,
            datetime.datetime.fromisoformat(identity["recording_ready_utc"]).timestamp(), time.time())
    except (package.PackageError, OSError, ValueError, zipfile.BadZipFile) as failure:
        error = failure
    finally:
        if recorder and recorder.poll() is None:
            recorder.send_signal(signal.SIGINT)
            try:
                recorder.wait(timeout=30)
            except subprocess.TimeoutExpired:
                recorder.kill()
                recorder.wait()
                error = error or package.PackageError("Recorder did not finalize after SIGINT")
        if recorder:
            reader.join(timeout=5)
        identity["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        identity_path.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
        (artifacts / "action-timeline.json").write_text(json.dumps(events, indent=2) + "\n", encoding="utf-8")
        # Only stop the device created above; never address `booted` or a user's device.
        try:
            package.run(["xcrun", "simctl", "shutdown", udid])
        except package.PackageError:
            pass
    if raw.is_file() and raw.stat().st_size:
        try:
            inspect_media(raw, artifacts)
        except (package.PackageError, ValueError, KeyError, OSError) as failure:
            error = error or failure
    else:
        error = error or package.PackageError("No actual recording was produced")
    if error:
        raise error


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--scenario", choices=("story", "qa", "all"), default="all")
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    try:
        takes = {"story": "DraftPocketDemoUITests/testCaptureProductionStory",
                 "qa": "DraftPocketDemoUITests/testQASwapDeleteUndoRestartAndChatIsolation"}
        for name, test_filter in takes.items():
            if args.scenario in (name, "all"):
                destination = args.artifacts.resolve() / name
                destination.mkdir(parents=True, exist_ok=False)
                capture(args.checkout.resolve(), args.configuration.resolve(), args.cache.resolve(), destination, test_filter)
    except (package.PackageError, OSError, ValueError, KeyError) as error:
        print("error: " + str(error), file=sys.stderr)
        sys.exit(1)
