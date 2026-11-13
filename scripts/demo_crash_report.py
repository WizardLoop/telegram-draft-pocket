"""Optional, bounded crash metadata for the isolated full-client demo only.

Apple documents IPS framing and fields (not a guaranteed report delivery time):
https://developer.apple.com/documentation/xcode/interpreting-the-json-format-of-a-crash-report
Apple's Simulator troubleshooting guidance names the host DiagnosticReports folder:
https://developer.apple.com/forums/thread/819560

Raw reports, paths, application messages, registers and queue names are never exported.
An absent/unsupported report is diagnostic unavailability, never proof of a successful run.
"""

import datetime
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
import uuid

import package


MAX_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 256
MAX_REPORTS = 16
MAX_FRAMES = 24
MAX_SCAN_SECONDS = 3.0
OUTPUT_NAME = "demo-crash-summary.json"


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _epoch(value):
    if type(value) not in (int, float) or not 0 <= value < 253402300800 or not math.isfinite(value):
        raise ValueError("Invalid timestamp")
    return float(value)


def _report_time(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Invalid report timestamp")
    parsed = datetime.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Report timestamp has no timezone")
    return _epoch(parsed.timestamp())


def _owned_process(path, udid, process_name):
    if not isinstance(path, str) or len(path) > 4096 or "\\" in path:
        return False
    if any(component in (".", "..") for component in path.split("/")):
        return False
    path = PurePosixPath(path)
    if not path.is_absolute() or len(path.parts) < 11:
        return False
    # Apple may redact a username in procPath. Match the complete device/app suffix,
    # not the user prefix, and never use the report's path to read any file.
    suffix = path.parts[-10:]
    if (suffix[:7] != ("CoreSimulator", "Devices", udid, "data", "Containers", "Bundle", "Application")
            or suffix[8:] != (process_name + ".app", process_name)):
        return False
    try:
        return str(uuid.UUID(suffix[7])).upper() == suffix[7]
    except ValueError:
        return False


def _integer(value):
    return type(value) is int and 0 <= value <= 0xffffffffffffffff


def _symbol(value):
    return (isinstance(value, str) and 1 <= len(value) <= 512
            and re.fullmatch(r"[A-Za-z0-9_.$@:+*?<>{}\[\](),=&!#~ \-]+", value) is not None
            and re.search(r"[A-Za-z_]", value) is not None)


def summarize_report(content, udid, bundle_id, process_name, started_utc, finished_utc):
    """Return only allowed fields for one exactly identified report, or None."""
    if not content or len(content) > MAX_BYTES:
        return None
    try:
        first, remainder = content.split(b"\n", 1)
        metadata = json.loads(first, object_pairs_hook=_pairs)
        report = json.loads(remainder, object_pairs_hook=_pairs)
        if not isinstance(metadata, dict) or not isinstance(report, dict):
            return None
        names = [metadata[key] for key in ("name", "app_name") if key in metadata]
        if (metadata.get("bug_type") != "309" or metadata.get("bundleID") != bundle_id
                or not names or any(name != process_name for name in names)
                or report.get("procName") != process_name
                or not isinstance(report.get("bundleInfo"), dict)
                or report["bundleInfo"].get("CFBundleIdentifier") != bundle_id
                or not _owned_process(report.get("procPath"), udid, process_name)):
            return None
        if "bug_type" in report and report["bug_type"] != "309":
            return None
        for key in ("isSimulated", "isNonFatal"):
            if key in report and report[key] not in (False, 0, "0", "false"):
                return None
        launched, crashed = _report_time(report.get("procLaunch")), _report_time(report.get("captureTime"))
        if not started_utc <= launched <= crashed <= finished_utc:
            return None
        result = {"launch_utc": launched, "crash_utc": crashed}
        exception = report.get("exception")
        if not isinstance(exception, dict):
            return None
        tokens = {}
        if isinstance(exception.get("type"), str) and re.fullmatch(r"EXC_[A-Z0-9_]{1,48}", exception["type"]):
            tokens["type"] = exception["type"]
        signal = exception.get("signal")
        if isinstance(signal, str):
            # Some system reports add an explanation in parentheses. Keep only the signal token.
            match = re.fullmatch(r"(SIG[A-Z0-9]{1,24})(?: \([^()\r\n]{1,120}\))?", signal)
            if match:
                tokens["signal"] = match[1]
        if not tokens:
            return None
        result["exception"] = tokens
        termination = report.get("termination")
        if isinstance(termination, dict):
            safe = {}
            namespace = termination.get("namespace")
            if isinstance(namespace, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", namespace):
                safe["namespace"] = namespace
            if _integer(termination.get("code")):
                safe["code"] = termination["code"]
            if safe:
                result["termination"] = safe
        threads = report.get("threads")
        index = report.get("faultingThread")
        if isinstance(threads, list) and type(index) is int and 0 <= index < len(threads):
            thread = threads[index]
            frames = thread.get("frames") if isinstance(thread, dict) else None
            if isinstance(frames, list):
                safe_frames = []
                for frame in frames[:MAX_FRAMES]:
                    if not isinstance(frame, dict):
                        continue
                    safe = {}
                    if _symbol(frame.get("symbol")):
                        safe["symbol"] = frame["symbol"]
                    for source, target in (("imageOffset", "image_offset"), ("symbolLocation", "symbol_offset")):
                        if _integer(frame.get(source)):
                            safe[target] = frame[source]
                    if safe:
                        safe_frames.append(safe)
                if safe_frames:
                    result["frames"] = safe_frames
        return result
    except (ValueError, TypeError, OverflowError, RecursionError, UnicodeDecodeError):
        return None


def _read_regular(path, before):
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_BINARY", 0))
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        actual = os.fstat(stream.fileno())
        if (not stat.S_ISREG(actual.st_mode) or actual.st_size > MAX_BYTES
                or (actual.st_dev, actual.st_ino) != (before.st_dev, before.st_ino)):
            return None
        return stream.read(MAX_BYTES + 1)


def export_crash_summaries(udid, bundle_id, started_utc, finished_utc, artifacts, *,
                           process_name="Telegram", reports_directory=None):
    """Export optional diagnostics; caller's native failure/success verdict stays authoritative."""
    try:
        udid = str(uuid.UUID(udid)).upper()
        started_utc, finished_utc = _epoch(started_utc), _epoch(finished_utc)
        if (started_utc > finished_utc or not isinstance(bundle_id, str)
                or not re.fullmatch(r"[A-Za-z0-9.-]{1,200}", bundle_id)
                or not isinstance(process_name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", process_name)):
            raise ValueError("Invalid diagnostic scope")
    except (ValueError, TypeError, AttributeError):
        raise package.PackageError("Invalid isolated crash diagnostic scope") from None
    directory = Path(reports_directory) if reports_directory is not None else Path.home() / "Library/Logs/DiagnosticReports"
    if not directory.exists():
        return {"status": "unavailable", "reason": "report_directory_absent"}
    if directory.is_symlink() or getattr(directory, "is_junction", lambda: False)() or not directory.is_dir():
        return {"status": "unavailable", "reason": "report_directory_invalid"}
    summaries, examined, limited = [], 0, False
    deadline = time.monotonic() + MAX_SCAN_SECONDS
    try:
        with os.scandir(directory) as entries:
            for entry_index, entry in enumerate(entries):
                if entry_index >= MAX_ENTRIES or examined >= MAX_REPORTS or time.monotonic() >= deadline:
                    limited = True
                    break
                if not entry.name.endswith(".ips") or entry.is_symlink():
                    continue
                try:
                    # DirEntry.stat has zero file IDs on Windows; lstat provides
                    # the identity subsequently checked against the open descriptor.
                    info = os.lstat(entry.path)
                    if (not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES
                            or not started_utc <= info.st_mtime <= finished_utc):
                        continue
                    examined += 1
                    content = _read_regular(entry.path, info)
                    if content is not None:
                        result = summarize_report(content, udid, bundle_id, process_name, started_utc, finished_utc)
                        if result is not None:
                            summaries.append(result)
                except OSError:
                    continue
    except OSError:
        return {"status": "unavailable", "reason": "report_directory_unreadable"}
    if not summaries:
        return {"status": "unavailable" if limited else "absent",
                "reason": "bounded_scan_limit" if limited else "no_matching_crash_report"}
    output = {"schema_version": 1, "diagnostic_only": True, "reports": summaries}
    destination = package.bounded(Path(artifacts), OUTPUT_NAME)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(output, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return {"status": "saved", "path": OUTPUT_NAME, "count": len(summaries), "scan_limited": limited}
