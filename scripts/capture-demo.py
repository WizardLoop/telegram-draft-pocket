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


SCREENSHOTS = {
    "testCaptureProductionStory": {
        "formatted-composer-before-put-aside": 1,
        "formatted-composer-restored-review-frames": 1,
        "local-enqueue-only-no-network-delivery": 2,
    },
    "testQASwapDeleteUndoRestartAndChatIsolation": {
        "production-delete-undo": 1,
        "production-composer-restored-after-relaunch-offline-fixture": 1,
    },
}


def native_reference(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_~+=/.-]{0,2047}", identifier):
        raise package.PackageError("Native xcresult contains an invalid object reference")
    return identifier


def selected_native_case(native_cases, test_filter):
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
    return cases[0]


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
            case = {"identifier": "/".join(names), "status": status}
            if "summaryRef" in node:
                case["summary_ref"] = native_reference(value(node["summaryRef"], "id"))
            cases.append(case)

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
        references.append(native_reference(identifier))
    if not references or len(set(references)) != len(references):
        raise package.PackageError("Native xcresult test references are empty or ambiguous")
    for reference in references:
        plans = read_reference(reference)
        for plan in values(plans, "summaries"):
            for testable in values(plan, "testableSummaries"):
                for test in values(testable, "tests"):
                    visit(test, [])
    return cases


def named_screenshots(summary, test_filter):
    """Select only explicit keepAlways attachments from our successful test case."""
    expected_method = test_filter.split("/", 1)[1]
    expected = SCREENSHOTS.get(expected_method)
    if expected is None:
        raise package.PackageError("Unknown screenshot scenario")

    def value(node, key):
        wrapper = node.get(key) if isinstance(node, dict) else None
        return wrapper.get("_value") if isinstance(wrapper, dict) else None

    identifier = value(summary, "identifier")
    if (not isinstance(identifier, str) or not re.search(r"(?<![A-Za-z0-9_])" + re.escape(expected_method)
            + r"(?![A-Za-z0-9_])", identifier) or value(summary, "testStatus") != "Success"):
        raise package.PackageError("Screenshot summary does not match the successful native test")
    selected, counts = [], dict.fromkeys(expected, 0)

    def visit(node, key, depth=0):
        if depth > 100 or not isinstance(node, dict):
            raise package.PackageError("Invalid native screenshot activity tree")
        wrapper = node.get(key, {"_values": []})
        activities = wrapper.get("_values") if isinstance(wrapper, dict) else None
        if not isinstance(activities, list):
            raise package.PackageError("Invalid native screenshot activity schema")
        for activity in activities:
            if not isinstance(activity, dict):
                raise package.PackageError("Invalid native screenshot activity")
            wrapper = activity.get("attachments", {"_values": []})
            attachments = wrapper.get("_values") if isinstance(wrapper, dict) else None
            if not isinstance(attachments, list):
                raise package.PackageError("Invalid native screenshot attachment schema")
            for attachment in attachments:
                name = value(attachment, "name")
                if not isinstance(name, str) or name not in expected:
                    continue
                uti = value(attachment, "uniformTypeIdentifier")
                extension = {"public.png": ".png", "public.jpeg": ".jpg"}.get(uti) if isinstance(uti, str) else None
                if extension is None:
                    raise package.PackageError("Named screenshot is not a PNG/JPEG attachment")
                reference = native_reference(value(attachment.get("payloadRef"), "id"))
                size = value(attachment, "payloadSize")
                if not isinstance(size, str) or not size.isdigit() or not 0 < int(size) <= 32 * 1024 * 1024:
                    raise package.PackageError("Named screenshot exceeds its payload limit or has invalid size")
                timestamp = value(attachment, "timestamp")
                if timestamp is not None:
                    try:
                        # Normalize Apple's +0000 offset for Python 3.10 as well.
                        normalized = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", timestamp.replace("Z", "+00:00"))
                        parsed = datetime.datetime.fromisoformat(normalized)
                        if parsed.tzinfo is None:
                            raise ValueError("Missing timezone")
                    except (ValueError, AttributeError):
                        raise package.PackageError("Named screenshot timestamp is invalid") from None
                counts[name] += 1
                selected.append({"name": name, "payload_ref": reference, "bytes": int(size),
                                 "uniform_type_identifier": uti, "timestamp": timestamp,
                                 "path": f"{name}-{counts[name]:02d}{extension}"})
            visit(activity, "subactivities", depth + 1)

    visit(summary, "activitySummaries")
    if counts != expected:
        raise package.PackageError("Native result lacks the exact named scenario screenshots")
    return selected


def export_named_screenshots(bundle, artifacts, case, test_filter, summary):
    selected = named_screenshots(summary, test_filter)
    destination = package.bounded(artifacts, "screenshots")
    if destination.exists():
        raise package.PackageError("Screenshot destination already exists")
    # Export to a private temporary directory before promoting validated image files.
    with tempfile.TemporaryDirectory(prefix="draft-pocket-screenshots-") as temporary:
        for item in selected:
            target = package.bounded(Path(temporary), item["path"])
            # Apple documents this legacy object export in Xcode 16.3 release notes.
            package.run(["xcrun", "xcresulttool", "export", "object", "--legacy", "--type", "file",
                         "--path", bundle, "--id", item["payload_ref"], "--output-path", target])
            if not target.is_file() or target.is_symlink() or target.stat().st_size != item["bytes"]:
                raise package.PackageError("Exported screenshot is not its declared regular payload")
            content = target.read_bytes()
            signature = b"\x89PNG\r\n\x1a\n" if item["uniform_type_identifier"] == "public.png" else b"\xff\xd8\xff"
            if not content.startswith(signature):
                raise package.PackageError("Exported screenshot content does not match its image type")
            probe = json.loads(package.run(["ffprobe", "-v", "error", "-show_streams", "-count_frames", "-of", "json", target]))
            streams = probe.get("streams", [])
            codec = "png" if item["uniform_type_identifier"] == "public.png" else "mjpeg"
            if (len(streams) != 1 or streams[0].get("codec_type") != "video" or streams[0].get("codec_name") != codec
                    or streams[0].get("nb_read_frames") != "1"
                    or any(not isinstance(streams[0].get(d), int) or not 0 < streams[0][d] <= 16384 for d in ("width", "height"))):
                raise package.PackageError("Exported screenshot is not a decodable single image")
            item["width"], item["height"] = streams[0]["width"], streams[0]["height"]
            item["sha256"] = hashlib.sha256(content).hexdigest()
        destination.mkdir()
        for item in selected:
            shutil.copyfile(Path(temporary) / item["path"], destination / item["path"])
    manifest = {"native_case": test_filter, "summary_ref": case["summary_ref"],
                "native_bundle_inventory": "../native-xcresult-files.json", "screenshots": selected,
                "note": "Unmodified explicitly named XCTest screenshots of the isolated fictional fixture; visual review is still required."}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


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


def native_results(test_directory, artifacts, redact, test_filter):
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
        case = selected_native_case(cases, test_filter)
        reference = native_reference(case.get("summary_ref"))
        # Keep activity text/diagnostics private. Only exact allowlisted screenshot
        # payloads and a minimal identity manifest are exported from this summary.
        summary = json.loads(package.run(command + ["--id", reference]))
        export_named_screenshots(bundle, artifacts, case, test_filter, summary)
        return cases


def verify_scenario(native_cases, events, test_filter, started, finished):
    """A generic passing test and an animated screen are not scenario evidence."""
    selected_native_case(native_cases, test_filter)
    expected_class, expected_method = test_filter.split("/", 1)
    if expected_method == "testCaptureProductionStory":
        required = ["story_start", "put_aside_start", "put_aside_end", "incoming_question_visible", "put_aside_start", "put_aside_end",
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
    return 1


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
        cases = native_results(test_directory, artifacts, redact, test_filter)
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
