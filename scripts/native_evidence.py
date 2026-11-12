#!/usr/bin/env python3
"""Review exact packaged XCTest cases against raw native logs, without running tests."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys


# This explicit inventory is independent of log contents and of success reports.
# Adding a suite or changing a registered filter requires a reviewed update here.
TARGETS = (
    ("//submodules/TelegramCore:DraftPocketTests", None),
    ("//submodules/ChatInterfaceState:DraftPocketComposerTests", None),
    ("//Tests/ComposerHarness:DraftPocketSelectionHarnessTests", "HarnessDraftPocketSelectionTests"),
    ("//submodules/TelegramUI/Components/Chat/ChatRichTextEditorComposer:DraftPocketSelectionBridgeTests",
     "DocumentSelectionBridgeTests"),
    ("//submodules/Postbox:DraftMediaRetentionTests", "MediaBoxDraftRetentionTests"),
)
SOURCES = (
    ("submodules/TelegramCore/Tests/DraftPocketTests.swift", "DraftPocketTests", TARGETS[0][0]),
    ("submodules/TelegramCore/Tests/DraftPocketMediaLifecycleTests.swift", "DraftPocketMediaLifecycleTests", TARGETS[0][0]),
    ("submodules/ChatInterfaceState/Tests/DraftPocketComposerStateTests.swift", "DraftPocketComposerStateTests", TARGETS[1][0]),
    ("Tests/ComposerHarness/Tests/HarnessDraftPocketSelectionTests.swift", "HarnessDraftPocketSelectionTests", TARGETS[2][0]),
    ("submodules/TelegramUI/Components/Chat/ChatRichTextEditorComposer/Tests/DocumentSelectionBridgeTests.swift",
     "DocumentSelectionBridgeTests", TARGETS[3][0]),
    ("submodules/Postbox/Tests/MediaBoxDraftRetentionTests.swift", "MediaBoxDraftRetentionTests", TARGETS[4][0]),
)
BOUNDARY = (
    "Exact raw XCTest case starts/outcomes plus registered target and lifecycle status. "
    "The caller must supply the package for the tested revision. This checks recorded evidence, "
    "not log authenticity, installed binaries, full-client UI, visual quality or another revision. "
    "The three separately run legacy UIKit editor cases are excluded. Hostless tests do not require xcresult."
)


class EvidenceError(Exception):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def package_file(root, relative):
    """All callers use fixed inventory paths; reject links within the package."""
    if relative not in {"Integration/BASE.json", "Integration/files.json", *(row[0] for row in SOURCES)}:
        raise EvidenceError("Source path is outside the native inventory: " + str(relative))
    current = root.resolve(strict=True)
    for part in relative.split("/"):
        current = current / part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise EvidenceError("Linked native inventory path: " + relative)
    if not current.is_file():
        raise EvidenceError("Missing native inventory file: " + relative)
    return current


def json_object(data, label):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise EvidenceError("Duplicate JSON key in " + label + ": " + key)
            result[key] = value
        return result

    result = json.loads(data.decode("utf-8"), object_pairs_hook=unique_pairs)
    if not isinstance(result, dict):
        raise EvidenceError(label + " must be a JSON object")
    return result


def swift_structure(text, label):
    """Mask the current fixtures' comments/ordinary strings; reject unfamiliar forms.

    This deliberately is not a Swift parser. Conditional declarations, raw or
    multiline strings and complex interpolation require review before adoption.
    Braces in comments and ordinary string values never count as source scopes.
    """
    output = list(text)
    index = 0
    while index < len(text):
        start = index
        if text.startswith("//", index):
            index = text.find("\n", index)
            if index < 0:
                index = len(text)
        elif text.startswith("/*", index):
            index += 2
            nesting = 1
            while index < len(text) and nesting:
                if text.startswith("/*", index):
                    nesting += 1
                    index += 2
                elif text.startswith("*/", index):
                    nesting -= 1
                    index += 2
                else:
                    index += 1
            if nesting:
                raise EvidenceError("Unterminated Swift comment: " + label)
        elif text[index] == '"':
            if text.startswith('"""', index) or (index and text[index - 1] == "#"):
                raise EvidenceError("Unsupported Swift string form: " + label)
            index += 1
            while index < len(text) and text[index] != '"':
                if text[index] in "\r\n":
                    raise EvidenceError("Unsupported Swift string form: " + label)
                if text.startswith("\\(", index):
                    interpolation = re.match(r"\\\([A-Za-z_][A-Za-z0-9_.]*\)", text[index:])
                    if not interpolation:
                        raise EvidenceError("Unsupported Swift interpolation: " + label)
                    index += len(interpolation[0])
                    continue
                index += 2 if text[index] == "\\" else 1
            if index >= len(text):
                raise EvidenceError("Unterminated Swift string: " + label)
            index += 1
        else:
            index += 1
            continue
        for offset in range(start, index):
            if text[offset] not in "\r\n":
                output[offset] = " "
    return "".join(output)


def authored_cases(data, class_name, path):
    text = data.decode("utf-8")
    if "\x00" in text or text.startswith("\ufeff"):
        raise EvidenceError("Invalid Swift source encoding: " + path)
    code = swift_structure(text, path)
    if re.search(r"^\s*#(?:if|elseif|else|endif)\b", code, re.MULTILINE):
        raise EvidenceError("Conditional XCTest declarations require review: " + path)
    classes = list(re.finditer(r"^final class ([A-Za-z_][A-Za-z0-9_]*): XCTestCase \{\s*$", code, re.MULTILINE))
    if len(classes) != 1 or classes[0][1] != class_name or len(re.findall(r"\bclass\s+\w+", code)) != 1:
        raise EvidenceError("Unexpected XCTest class structure: " + path)
    depths = []
    depth = 0
    for char in code:
        depths.append(depth)
        depth += (char == "{") - (char == "}")
        if depth < 0:
            raise EvidenceError("Unbalanced Swift braces: " + path)
    if depth or depths[classes[0].start()] != 0:
        raise EvidenceError("Unbalanced or nested XCTest class: " + path)
    methods = list(re.finditer(r"^    func (test[A-Za-z0-9_]+)\(\)(?: async)?(?: throws)? \{\s*$", code, re.MULTILINE))
    candidates = re.findall(r"\bfunc\s+(test[A-Za-z0-9_]*)\b", code)
    class_end = next(index for index in range(code.index("{", classes[0].start()) + 1, len(code))
                     if code[index] == "}" and depths[index] == 1)
    if (not methods or len(methods) != len(candidates) or len(candidates) != len(set(candidates))
            or any(depths[method.start()] != 1 or not classes[0].start() < method.start() < class_end for method in methods)):
        raise EvidenceError("Unexpected XCTest method structure: " + path)
    return [class_name + "/" + method[1] for method in methods]


def target_identity(entry, *, registration=False):
    if registration and isinstance(entry, str):
        return entry, None
    if not isinstance(entry, dict) or "target" not in entry or "filter" not in entry:
        raise EvidenceError("Malformed native target identity")
    if registration and set(entry) != {"target", "filter"}:
        raise EvidenceError("Unexpected native target registration fields")
    target, selection = entry["target"], entry["filter"]
    if not isinstance(target, str) or (selection is not None and not isinstance(selection, str)):
        raise EvidenceError("Malformed native target identity")
    return target, selection


def load_inventory(package_root):
    root = Path(package_root)
    base_bytes = package_file(root, "Integration/BASE.json").read_bytes()
    manifest_bytes = package_file(root, "Integration/files.json").read_bytes()
    base = json_object(base_bytes, "BASE.json")
    manifest = json_object(manifest_bytes, "files.json")
    if (base.get("schema_version") != 1 or manifest.get("schema_version") != 1
            or not isinstance(base.get("commit"), str) or not re.fullmatch(r"[0-9a-f]{40}", base["commit"])
            or manifest.get("base_commit") != base["commit"]):
        raise EvidenceError("Native inventory schema/base mismatch")
    entries = base.get("native_test_targets")
    if not isinstance(entries, list):
        raise EvidenceError("Missing native target registrations")
    registered = [target_identity(entry, registration=True) for entry in entries]
    if Counter(registered) != Counter(TARGETS):
        raise EvidenceError("Native target registrations or filters differ from the reviewed inventory")
    feature = manifest.get("feature")
    files = feature.get("files") if isinstance(feature, dict) else None
    if not isinstance(files, list) or any(not isinstance(entry, dict) for entry in files):
        raise EvidenceError("Malformed feature file manifest")
    expected = {}
    sources = []
    for path, class_name, target in SOURCES:
        matches = [entry for entry in files if entry.get("source") == path or entry.get("target") == path]
        if (len(matches) != 1 or matches[0].get("source") != path or matches[0].get("target") != path
                or matches[0].get("action") != "copy"):
            raise EvidenceError("Missing, duplicate or redirected native source: " + path)
        data = package_file(root, path).read_bytes()
        sha = digest(data)
        if matches[0].get("sha256") != sha:
            raise EvidenceError("Native source hash mismatch: " + path)
        cases = authored_cases(data, class_name, path)
        if expected.keys() & set(cases):
            raise EvidenceError("Duplicate authored XCTest identity: " + path)
        expected.update({case: target for case in cases})
        sources.append({"path": path, "sha256": sha, "class": class_name, "target": target, "cases": cases})
    return {"expected": expected, "source_identity": sources, "base_commit": base["commit"],
            "base_sha256": digest(base_bytes), "manifest_sha256": digest(manifest_bytes)}


def display_target(target, selection):
    return target + (" [" + selection + "]" if selection is not None else "")


CASE_LINE = re.compile(
    r"Test Case '-\[(?:(?P<module>[A-Za-z_][A-Za-z0-9_]*)\.)?"
    r"(?P<class>[A-Za-z_][A-Za-z0-9_]*) (?P<method>test[A-Za-z0-9_]+)\]' "
    r"(?:(?P<started>started)\.|(?P<result>passed|failed|skipped) \((?P<seconds>[0-9]+(?:\.[0-9]+)?) seconds\)\.)"
)


def inspect_log(text, expected):
    labels = {display_target(*item): item[0] for item in TARGETS}
    starts, outcomes, boundaries = {}, {}, Counter()
    completions = {target: [] for target, _ in TARGETS}
    errors = []
    active = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith("Starting native test target:"):
            if active is not None:
                errors.append(f"Line {number}: previous target has no completion marker: {active}")
            label = line.removeprefix("Starting native test target:").strip()
            active = labels.get(label)
            if active is None:
                errors.append(f"Line {number}: unexpected target boundary {label!r}")
            else:
                boundaries[active] += 1
            continue
        if line.startswith("Native test target PASSED:"):
            label = line.removeprefix("Native test target PASSED:").strip()
            target = labels.get(label)
            if target is None or active != target:
                errors.append(f"Line {number}: success marker is outside its registered target")
            if target is not None:
                completions[target].append({"line": number, "stage": "test", "result": "passed"})
            active = None
            continue
        if line.startswith("Native test target FAILED:"):
            marker = re.fullmatch(r"Native test target FAILED: (.+) \[(build|test)\]:[ \t]*(.*)", line)
            target = labels.get(marker[1]) if marker else None
            if target is None:
                errors.append(f"Line {number}: unrecognized native target failure marker")
            else:
                stage = marker[2]
                if (stage == "test" and active != target) or (stage == "build" and active is not None):
                    errors.append(f"Line {number}: failure marker is outside its registered target")
                completions[target].append({"line": number, "stage": stage, "result": "failed"})
            active = None
            continue
        if not line.startswith("Test Case"):
            continue
        match = CASE_LINE.fullmatch(line)
        if not match:
            errors.append(f"Line {number}: unrecognized XCTest case line")
            continue
        case = match["class"] + "/" + match["method"]
        if active is None or expected.get(case) != active:
            errors.append(f"Line {number}: case {case} is outside its registered target")
        observation = {"line": number, "target": active, "module": match["module"]}
        if match["started"]:
            starts.setdefault(case, []).append(observation)
        else:
            observation.update(result=match["result"], seconds=match["seconds"])
            outcomes.setdefault(case, []).append(observation)
            if len(starts.get(case, [])) != len(outcomes[case]):
                errors.append(f"Line {number}: case outcome lacks a matching start: {case}")
    for target, _ in TARGETS:
        if boundaries[target] != 1:
            errors.append(f"Expected one execution boundary for {target}; observed {boundaries[target]}")
    return starts, outcomes, completions, errors


def review_native_evidence(package_root, log_path, target_results_path):
    """Always return a review; invalid inputs and incomplete evidence fail closed."""
    review = {"schema_version": 1, "complete_native_case_evidence": False, "boundary": BOUNDARY,
              "expected_count": None, "passed_count": 0, "input_errors": [], "evidence_errors": []}
    try:
        inventory = load_inventory(package_root)
        expected = inventory.pop("expected")
        review.update(inventory, expected_count=len(expected))
        raw = Path(log_path).read_bytes()
        review["native_log_sha256"] = digest(raw)
        log = raw.decode("utf-8")
        target_bytes = Path(target_results_path).read_bytes()
        review["target_results_sha256"] = digest(target_bytes)
        target_report = json_object(target_bytes, "native target results")
        records = target_report.get("targets")
        if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
            raise EvidenceError("Native target results must contain target objects")
        identities = [target_identity(item) for item in records]
        if Counter(identities) != Counter(TARGETS):
            raise EvidenceError("Native target results or filters differ from the reviewed inventory")
        lifecycle = target_report.get("lifecycle_errors")
        if not isinstance(lifecycle, list) or any(not isinstance(item, str) for item in lifecycle):
            raise EvidenceError("Native target results must contain lifecycle_errors")
        starts, outcomes, completions, errors = inspect_log(log, expected)
        for record in records:
            target = record["target"]
            events = completions[target]
            for event in events:
                if record.get(event["stage"]) != event["result"]:
                    errors.append(f"Line {event['line']}: raw {event['stage']} {event['result']} "
                                  f"contradicts target results for {target}")
            if record.get("test") == "passed" and (len(events) != 1 or events[0]["stage"] != "test"
                                                    or events[0]["result"] != "passed"):
                errors.append(f"Expected exactly one passed completion for {target}")
        observed = starts.keys() | outcomes.keys()
        passed = sorted(case for case, target in expected.items()
                        if len(starts.get(case, [])) == len(outcomes.get(case, [])) == 1
                        and starts[case][0]["target"] == outcomes[case][0]["target"] == target
                        and outcomes[case][0]["result"] == "passed")
        review.update(
            passed_count=len(passed), passed=passed,
            missing=sorted(expected.keys() - outcomes.keys()),
            failed_or_skipped=sorted(case for case in expected
                                     if any(item["result"] != "passed" for item in outcomes.get(case, []))),
            repeated_outcomes=sorted(case for case in observed
                                     if len(outcomes.get(case, [])) > 1 or len(starts.get(case, [])) > 1),
            unexpected_cases=sorted(observed - expected.keys()),
            observed_starts=starts, observed_outcomes=outcomes, evidence_errors=errors,
            observed_target_completions=completions,
            targets=records, lifecycle_errors=lifecycle,
            all_targets_passed=all(item.get("build") == item.get("test") == "passed" for item in records),
        )
        review["complete_native_case_evidence"] = (
            len(passed) == len(expected) and not errors and not review["unexpected_cases"]
            and not review["repeated_outcomes"] and review["all_targets_passed"] and not lifecycle
        )
    except (EvidenceError, OSError, UnicodeError, ValueError) as error:
        review["input_errors"].append(str(error))
    return review


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--targets", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    # A review must never overwrite its own source evidence or package inputs.
    protected = [args.log, args.targets, args.package / "Integration/BASE.json", args.package / "Integration/files.json"]
    protected.extend(args.package / row[0] for row in SOURCES)
    if args.output.resolve() in {path.resolve() for path in protected}:
        parser.error("Output must differ from all evidence and inventory inputs")
    review = review_native_evidence(args.package, args.log, args.targets)
    try:
        args.output.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        print("Could not save native evidence review: " + str(error), file=sys.stderr)
        return 1
    print(json.dumps({key: review[key] for key in ("expected_count", "passed_count", "complete_native_case_evidence", "input_errors")}))
    return 0 if review["complete_native_case_evidence"] else 1


if __name__ == "__main__":
    sys.exit(main())
