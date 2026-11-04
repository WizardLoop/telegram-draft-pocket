#!/usr/bin/env python3
"""Reproducible Draft Pocket overlay packaging; no iOS validation is implied."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
FEATURE_NATIVE_SOURCES = {"Tests/ComposerHarness/Tests/HarnessDraftPocketSelectionTests.swift"}


def feature_source_allowed(path):
    return path.startswith("submodules/") or path in FEATURE_NATIVE_SOURCES


def native_test_spec(entry):
    target = entry if isinstance(entry, str) else entry.get("target") if isinstance(entry, dict) else None
    selection = entry.get("filter") if isinstance(entry, dict) else None
    if not isinstance(target, str) or not re.fullmatch(r"//[A-Za-z0-9_./+-]+:[A-Za-z0-9_.+-]+", target):
        raise PackageError("Invalid native test target in BASE.json")
    if isinstance(entry, dict) and (set(entry) != {"target", "filter"} or not isinstance(selection, str)
                                   or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*(?:/[A-Za-z_][A-Za-z0-9_]*)?", selection)):
        raise PackageError("Invalid native XCTest class/method filter in BASE.json")
    return target, selection


class PackageError(Exception):
    pass


def run(args, cwd=None, capture=True):
    environment = None
    if Path(str(args[0])).stem.lower() == "git":
        environment = os.environ.copy()
        # Every command is addressed to an explicit checkout. Inherited worktree/
        # index overrides must not redirect a rehearsal or install to another tree.
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
            environment.pop(key, None)
    result = subprocess.run([str(arg) for arg in args], cwd=cwd, check=False,
                            env=environment,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None)
    if result.returncode:
        error = result.stderr.decode("utf-8", errors="replace").strip() if capture else ""
        raise PackageError(f"{args[0]} exited {result.returncode}: {error}")
    return result.stdout if capture else b""


def git(checkout, *args):
    return run(["git", "-c", "core.quotePath=false", "-C", checkout, *args])


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as error:
        raise PackageError(f"Cannot read {path.name}: {error}") from error


def digest(data):
    return hashlib.sha256(data).hexdigest()


def relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise PackageError(f"Unsafe manifest path: {value!r}")
    parts = value.split("/")
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    if any(part in ("", ".", "..") or part.lower() == ".git" or part.endswith((".", " "))
           or part.split(".")[0].lower() in reserved for part in parts):
        raise PackageError(f"Unsafe manifest path: {value!r}")
    if any(ord(char) < 32 for char in value) or PurePosixPath(value).is_absolute():
        raise PackageError(f"Unsafe manifest path: {value!r}")
    return value


def bounded(root, value):
    """Reject every link component, including Windows directory junctions."""
    relative(value)
    root = root.resolve(strict=True)
    path = root
    for component in value.split("/"):
        path = path / component
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise PackageError(f"Link is not allowed in package path: {value}")
    if not path.resolve(strict=False).is_relative_to(root):
        raise PackageError(f"Path escapes root: {value}")
    return path


def check_hash(path, expected):
    if not re.fullmatch(r"[0-9a-f]{64}", expected or ""):
        raise PackageError(f"Invalid SHA-256 for {path.name}")
    if not path.is_file() or digest(path.read_bytes()) != expected:
        raise PackageError(f"Hash mismatch or missing file: {path}")


def base_config(root):
    base = load_json(root / "Integration/BASE.json")
    if base.get("schema_version") != 1 or not re.fullmatch(r"[0-9a-f]{40}", base.get("commit", "")):
        raise PackageError("BASE.json must have schema_version 1 and a full commit SHA")
    if base.get("upstream_url") != "https://github.com/TelegramMessenger/Telegram-iOS.git":
        raise PackageError("Unexpected upstream URL; only the documented read-only upstream is supported")
    targets = base.get("native_test_targets", [])
    if not isinstance(targets, list):
        raise PackageError("native_test_targets must be a list")
    for entry in targets:
        native_test_spec(entry)
    return base


def sections(manifest, with_demo):
    result = [manifest["feature"]]
    if with_demo:
        demo = manifest.get("demo", {})
        if not demo.get("available"):
            raise PackageError("Demo is unavailable: " + demo.get("reason", "no packaged bootstrap"))
        result.append(demo)
    return result


def patch_targets(data):
    """Only modifications of existing regular text files are accepted."""
    text = data.decode("utf-8")
    targets = []
    for line in text.splitlines():
        if line.startswith("diff --git "):
            match = re.fullmatch(r"diff --git a/([^\s]+) b/([^\s]+)", line)
            if not match or match[1] != match[2]:
                raise PackageError("Patch renames and quoted paths are not supported")
            targets.append(relative(match[1]))
        elif line.startswith(("new file mode ", "deleted file mode ", "rename ",
                              "copy ", "old mode ", "new mode ", "GIT binary patch")):
            raise PackageError("Patch may only modify existing text files; put new files in the overlay")
    if not targets or len(set(targets)) != len(targets):
        raise PackageError("Empty patch or repeated patch target")
    return targets


def validate_package(root, with_demo=False, manifest_override=None):
    base = base_config(root)
    manifest = manifest_override if manifest_override is not None else load_json(root / "Integration/files.json")
    if manifest.get("schema_version") != 1 or manifest.get("base_commit") != base["commit"]:
        raise PackageError("Manifest schema/base does not match BASE.json")
    seen_sources = set()
    seen_new = set()
    target_spellings = {}
    for index, section in enumerate(sections(manifest, with_demo)):
        this_targets = set()
        for entry in section.get("files", []):
            source, target = relative(entry["source"]), relative(entry["target"])
            spelling = target_spellings.setdefault(target.casefold(), target)
            if spelling != target:
                raise PackageError("Target paths collide on a case-insensitive filesystem")
            if (index and not source.startswith("Demo/Overlay/")) or (not index and not feature_source_allowed(source)):
                raise PackageError(f"Overlay source is outside the explicit feature/demo source locations: {source}")
            if entry.get("action") != "copy" or source in seen_sources or target in seen_new:
                raise PackageError("Invalid or duplicated overlay entry")
            if index == 0 and source != target:
                raise PackageError("Feature overlays must use the exact upstream target path")
            if index and source != "Demo/Overlay/" + target:
                raise PackageError("Demo overlays must map their relative path to the same upstream path")
            check_hash(bounded(root, source), entry["sha256"])
            this_targets.add(target)
            seen_sources.add(source)
            seen_new.add(target)
        patch = section.get("patch")
        expected_path = "Integration/demo.patch" if index else "Integration/telegram-ios.patch"
        if not patch and bounded(root, expected_path).exists():
            raise PackageError(f"Unlisted patch exists: {expected_path}")
        if patch:
            if patch["source"] != expected_path:
                raise PackageError("Unexpected patch source")
            patch_path = bounded(root, patch["source"])
            check_hash(patch_path, patch["sha256"])
            targets = patch_targets(patch_path.read_bytes())
            entries = patch.get("files", [])
            if set(targets) != {entry["target"] for entry in entries} or len(targets) != len(entries):
                raise PackageError("Patch targets differ from manifest")
            for entry in entries:
                target = relative(entry["target"])
                spelling = target_spellings.setdefault(target.casefold(), target)
                if spelling != target:
                    raise PackageError("Target paths collide on a case-insensitive filesystem")
                if entry.get("action") != "modify" or target in this_targets:
                    raise PackageError("An overlay target cannot also be added/modified by the same patch")
                if not index and target in seen_new:
                    raise PackageError("Feature patch overlaps its overlay")
                for key in ("before_sha256", "after_sha256"):
                    if not re.fullmatch(r"[0-9a-f]{64}", entry.get(key, "")):
                        raise PackageError(f"Invalid {key}")
        if not section.get("files") and not patch:
            raise PackageError("Requested package section contains no implementation")
    # Inventory checks prevent a new source being left out of the manifest.
    actual_sources = set()
    for folder in ("submodules", "Tests/ComposerHarness", "Demo/Overlay") if with_demo else ("submodules", "Tests/ComposerHarness"):
        directory = root / folder
        if directory.exists():
            for path in directory.rglob("*"):
                if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                    raise PackageError("Overlay contains a link")
                if path.is_file():
                    actual_sources.add(path.relative_to(root).as_posix())
    if actual_sources != seen_sources:
        raise PackageError("Overlay inventory differs from manifest: " + ", ".join(sorted(actual_sources ^ seen_sources)))
    return base, manifest


def all_expected(manifest, with_demo=False):
    expected = {}
    for section in sections(manifest, with_demo):
        expected.update({e["target"]: e["sha256"] for e in section.get("files", [])})
        expected.update({e["target"]: e["after_sha256"] for e in section.get("patch", {}).get("files", [])})
    return expected


def changed_paths(checkout):
    tracked = git(checkout, "diff", "--name-only", "-z", "HEAD").decode("utf-8").split("\0")
    untracked = git(checkout, "ls-files", "--others", "--exclude-standard", "-z").decode("utf-8").split("\0")
    return {name for name in tracked + untracked if name}


def verify_head(checkout, base):
    top = Path(git(checkout, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if top != checkout.resolve():
        raise PackageError("Target must be the checkout root, not a directory inside another checkout")
    if git(checkout, "rev-parse", "HEAD").decode().strip() != base["commit"]:
        raise PackageError("Checkout HEAD differs from pinned BASE.json; prepare a separate clean checkout")
    if git(checkout, "diff", "--cached", "--name-only").strip():
        raise PackageError("Checkout has staged changes; use a separate checkout")


def is_applied(checkout, expected):
    if changed_paths(checkout) != set(expected):
        return False
    return all(bounded(checkout, path).is_file() and digest(bounded(checkout, path).read_bytes()) == sha
               for path, sha in expected.items())


def prepare(root, checkout, with_demo=False, require_clean=False):
    base, manifest = validate_package(root, with_demo)
    checkout = checkout.resolve(strict=True)
    verify_head(checkout, base)
    expected = all_expected(manifest, with_demo)
    if not require_clean and is_applied(checkout, expected):
        return manifest, True, False
    feature_applied = with_demo and not require_clean and is_applied(checkout, all_expected(manifest))
    if changed_paths(checkout) and not feature_applied:
        raise PackageError("Checkout is dirty or partially applied; no files changed. Use a fresh checkout")
    # Rehearse the full ordered application against only the affected base files.
    # This directory is disposable; the user's checkout is untouched during preflight.
    with tempfile.TemporaryDirectory(prefix="draft-pocket-preflight-") as temporary:
        scratch = Path(temporary)
        # A real repository anchors relative patch paths even when TMPDIR lies
        # inside another worktree. Explicit LF handling defeats host autocrlf.
        run(["git", "-c", "core.autocrlf=false", "init", "--quiet"], cwd=scratch)
        git(scratch, "config", "core.autocrlf", "false")
        if feature_applied:
            for path in all_expected(manifest):
                staged = bounded(scratch, path)
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(bounded(checkout, path).read_bytes())
        for section in sections(manifest, with_demo)[1 if feature_applied else 0:]:
            for entry in section.get("files", []):
                destination = bounded(checkout, entry["target"])
                if destination.exists():
                    raise PackageError(f"New-file collision: {entry['target']}")
                staged = bounded(scratch, entry["target"])
                if staged.exists():
                    raise PackageError("Overlay collision across sections")
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(bounded(root, entry["source"]).read_bytes())
            patch = section.get("patch")
            if patch:
                for entry in patch["files"]:
                    original = bounded(checkout, entry["target"])
                    staged = bounded(scratch, entry["target"])
                    if not staged.exists():
                        check_hash(original, entry["before_sha256"])
                        staged.parent.mkdir(parents=True, exist_ok=True)
                        staged.write_bytes(original.read_bytes())
                    check_hash(staged, entry["before_sha256"])
                patch_path = bounded(root, patch["source"])
                run(["git", "-c", "core.autocrlf=false", "apply", "--check", "--whitespace=error", patch_path], cwd=scratch)
                run(["git", "-c", "core.autocrlf=false", "apply", "--whitespace=error", patch_path], cwd=scratch)
                for entry in patch["files"]:
                    check_hash(bounded(scratch, entry["target"]), entry["after_sha256"])
        for path, sha in expected.items():
            check_hash(bounded(scratch, path), sha)
    return manifest, False, feature_applied


def apply(root, checkout, with_demo=False, require_clean=False):
    manifest, already, feature_applied = prepare(root, checkout, with_demo, require_clean)
    if already:
        print("Already applied: every target hash and the complete changed-file inventory verified.")
        return
    # All checks complete before the first write. I/O failures remain failures and are
    # never treated as an applied package; do not reset/clean a partially written tree.
    for section in sections(manifest, with_demo)[1 if feature_applied else 0:]:
        for entry in section.get("files", []):
            destination = bounded(checkout, entry["target"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as output:
                output.write(bounded(root, entry["source"]).read_bytes())
        if section.get("patch"):
            run(["git", "-c", "core.autocrlf=false", "apply", "--whitespace=error", bounded(root, section["patch"]["source"])], cwd=checkout)
    if not is_applied(checkout, all_expected(manifest, with_demo)):
        raise PackageError("Post-application verification failed; preserve this tree for inspection and use a new checkout")
    print("Package applied; all target hashes and changed-file inventory verified. iOS build/runtime NOT RUN.")


def bootstrap(root, destination, submodules=False):
    base = base_config(root)
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise PackageError("Destination already exists; bootstrap never overwrites an existing directory")
    if not shutil.which("git"):
        raise PackageError("Git is required")
    destination.mkdir(parents=True)
    git(destination, "init")
    git(destination, "config", "core.autocrlf", "false")
    git(destination, "remote", "add", "origin", base["upstream_url"])
    git(destination, "fetch", "--depth=1", "origin", base["commit"])
    git(destination, "checkout", "--detach", "FETCH_HEAD")
    verify_head(destination, base)
    if submodules:
        # No --remote: every checkout remains the commit recorded by its parent.
        # Stream potentially long downloads so CI does not look idle for minutes.
        print("Fetching exact pinned submodules with shallow parallel clones.", flush=True)
        run(["git", "-C", destination, "submodule", "update", "--init", "--checkout",
             "--recursive", "--depth=1", "--jobs=4"], capture=False)
    print(f"Prepared upstream commit {base['commit']}. Submodules initialized: {submodules}.")
    if platform.system() != "Darwin":
        print("Build prerequisite missing: macOS and the pinned Xcode. Packaging checks are available here.")


def export(root, checkout, paths_file, invalidate_demo=False):
    base = base_config(root)
    verify_head(checkout, base)
    paths = load_json(paths_file)
    if paths.get("schema_version") != 1:
        raise PackageError("Export path list must have schema_version 1")
    new = [relative(path) for path in paths.get("feature_new", [])]
    modified = [relative(path) for path in paths.get("feature_modified", [])]
    if not new and not modified:
        raise PackageError("Export allowlist contains no implementation")
    if len(set(new + modified)) != len(new + modified):
        raise PackageError("Export allowlist has duplicate paths")
    if len({path.casefold() for path in new + modified}) != len(new + modified):
        raise PackageError("Export paths collide on a case-insensitive filesystem")
    if changed_paths(checkout) != set(new + modified):
        raise PackageError("Integration changes differ from the explicit export allowlist: " +
                           ", ".join(sorted(changed_paths(checkout) ^ set(new + modified))))
    tracked = set(git(checkout, "ls-tree", "-r", "--name-only", base["commit"]).decode("utf-8").splitlines())
    if any(path in tracked or not feature_source_allowed(path) for path in new):
        raise PackageError("New feature files must be untracked paths in an explicitly allowed source location")
    if any(path not in tracked for path in modified):
        raise PackageError("Modified feature files must exist at the pinned base")
    copied = []
    payloads = {}
    for path in new:
        payloads[path] = bounded(checkout, path).read_bytes()
        copied.append({"action": "copy", "source": path, "target": path, "sha256": digest(payloads[path])})
    changes = []
    originals = {}
    patch_data = b""
    if modified:
        patch_data = git(checkout, "diff", "--no-ext-diff", "--no-renames", "--binary", "--full-index", base["commit"], "--", *modified)
        if set(patch_targets(patch_data)) != set(modified):
            raise PackageError("Export patch omitted a listed file")
        for path in modified:
            originals[path] = git(checkout, "show", f"{base['commit']}:{path}")
            changes.append({"action": "modify", "target": path,
                            "before_sha256": digest(originals[path]),
                            "after_sha256": digest(bounded(checkout, path).read_bytes())})
        # Git attributes/autocrlf can normalize diff content while the working
        # file still has CRLF or mixed endings. Prove the actual patch recreates
        # the exact authored bytes before writing any package output.
        with tempfile.TemporaryDirectory(prefix="draft-pocket-export-preflight-") as temporary:
            scratch = Path(temporary)
            run(["git", "-c", "core.autocrlf=false", "init", "--quiet"], cwd=scratch)
            for path, data in originals.items():
                staged = bounded(scratch, path)
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(data)
            rehearsal_patch = scratch / ".git/export.patch"
            rehearsal_patch.write_bytes(patch_data)
            run(["git", "-c", "core.autocrlf=false", "apply", "--check", "--whitespace=error", rehearsal_patch], cwd=scratch)
            run(["git", "-c", "core.autocrlf=false", "apply", "--whitespace=error", rehearsal_patch], cwd=scratch)
            for entry in changes:
                if digest(bounded(scratch, entry["target"]).read_bytes()) != entry["after_sha256"]:
                    raise PackageError(f"Export patch roundtrip differs from development bytes: {entry['target']}. "
                                       "Review Git text attributes and normalize this development text file to LF, then re-export. "
                                       "No package outputs were changed.")
    feature = {"files": copied}
    if patch_data:
        feature["patch"] = {"source": "Integration/telegram-ios.patch", "sha256": digest(patch_data), "files": changes}
    manifest = {"schema_version": 1, "base_commit": base["commit"], "feature": feature,
                "demo": {"available": False, "reason": "No isolated full-client offline bootstrap has been implemented or validated."}}
    existing_manifest = root / "Integration/files.json"
    if existing_manifest.exists() and load_json(existing_manifest).get("demo", {}).get("available") and not invalidate_demo:
        raise PackageError("Demo export requires a separately reviewed feature/demo commit boundary; refusing to overwrite it")
    if invalidate_demo:
        manifest["demo"] = {"available": False, "reason": "Feature was re-exported. Revalidate demo input hashes and run export-demo before applying or building the demo."}
    # Preflight every destination before writing any export artifact.
    for path in [*payloads, "Integration/telegram-ios.patch", "Integration/files.json"]:
        bounded(root, path)
    actual_overlay = set()
    for folder in ("submodules", "Tests/ComposerHarness"):
        for path in (root / folder).rglob("*"):
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise PackageError("Existing overlay contains a link; export did not change it")
            if path.is_file():
                actual_overlay.add(path.relative_to(root).as_posix())
    if actual_overlay - set(new):
        raise PackageError("Export would leave stale overlay files; review the allowlist first")
    if not patch_data and (root / "Integration/telegram-ios.patch").exists():
        raise PackageError("Export would leave a stale feature patch; review the allowlist first")
    for path, data in payloads.items():
        output = bounded(root, path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
    (root / "Integration").mkdir(exist_ok=True)
    if patch_data:
        (root / "Integration/telegram-ios.patch").write_bytes(patch_data)
    (root / "Integration/files.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    validate_package(root)
    print(f"Exported {len(new)} new files and {len(modified)} modified files from the integration tree.")


def export_demo(root, reference, paths_file):
    """Seal a separately authored demo patch against the exact feature package.

    Demo source/patch artifacts are authored independently. Only the manifest is
    written here, after all input hashes and ordered application have passed.
    """
    base, manifest = validate_package(root)
    verify_head(reference, base)
    if not is_applied(reference, all_expected(manifest)):
        raise PackageError("Demo export requires an exact feature-only reference checkout")
    paths = load_json(paths_file)
    if paths.get("schema_version") != 1:
        raise PackageError("Demo export path list must have schema_version 1")
    new = [relative(p) for p in paths.get("demo_new", [])]
    modified = [relative(p) for p in paths.get("demo_modified", [])]
    if not new or not modified or len({p.casefold() for p in new + modified}) != len(new + modified):
        raise PackageError("Demo export needs unique new and modified paths")
    before_hashes = paths.get("feature_sha256", {})
    if set(before_hashes) != set(modified):
        raise PackageError("Demo export requires exact feature input hashes for every modified target")
    patch_path = bounded(root, "Integration/demo.patch")
    patch_data = patch_path.read_bytes()
    if set(patch_targets(patch_data)) != set(modified):
        raise PackageError("Demo patch differs from its explicit export allowlist")
    feature_expected = all_expected(manifest)
    copied, changes = [], []
    with tempfile.TemporaryDirectory(prefix="draft-pocket-demo-export-") as temporary:
        scratch = Path(temporary)
        run(["git", "-c", "core.autocrlf=false", "init", "--quiet"], cwd=scratch)
        for path in modified:
            # Base blobs provide canonical LF bytes even on a Windows host.
            original = (bounded(reference, path).read_bytes() if path in feature_expected else
                        git(reference, "show", f"{base['commit']}:{path}"))
            if digest(original) != before_hashes[path]:
                raise PackageError(f"Demo was authored against a stale feature input: {path}")
            target = bounded(scratch, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original)
        for path in new:
            if path in feature_expected or git(reference, "ls-files", "--", path).strip():
                raise PackageError(f"Demo new-file collision: {path}")
            source = "Demo/Overlay/" + path
            payload = bounded(root, source).read_bytes()
            target = bounded(scratch, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            copied.append({"action": "copy", "source": source, "target": path, "sha256": digest(payload)})
        run(["git", "-c", "core.autocrlf=false", "apply", "--check", "--whitespace=error", patch_path], cwd=scratch)
        run(["git", "-c", "core.autocrlf=false", "apply", "--whitespace=error", patch_path], cwd=scratch)
        for path in modified:
            changes.append({"action": "modify", "target": path, "before_sha256": before_hashes[path],
                            "after_sha256": digest(bounded(scratch, path).read_bytes())})
    manifest["demo"] = {"available": True, "files": copied,
                        "patch": {"source": "Integration/demo.patch", "sha256": digest(patch_data), "files": changes},
                        "native_test_target": "//Tests/DraftPocketDemo:DraftPocketDemoUITests",
                        "device_type": "iPhone 17", "os_version": "27.0"}
    validate_package(root, with_demo=True, manifest_override=manifest)
    bounded(root, "Integration/files.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Exported demo: {len(new)} new and {len(modified)} modified files. Native demo NOT RUN.")


def toolchain(root, checkout, configuration, with_demo=False):
    base, manifest = validate_package(root, with_demo)
    verify_head(checkout, base)
    if not is_applied(checkout, all_expected(manifest, with_demo)):
        raise PackageError("Build/test requires the exactly reconstructed requested package")
    if platform.system() != "Darwin":
        raise PackageError("iOS build/native tests require macOS and Xcode; this is not an iOS validation")
    if platform.machine() != "arm64":
        raise PackageError("The pinned Make.py simulator configuration requires an Apple Silicon Mac")
    macos = run(["sw_vers", "-productVersion"]).decode().strip()
    if macos.split(".")[0] != base["versions"]["macos"].split(".")[0]:
        raise PackageError(f"Pinned macOS {base['versions']['macos']} required; found {macos}")
    xcode = run(["xcodebuild", "-version"]).decode().splitlines()[0]
    if xcode != "Xcode " + base["versions"]["xcode"]:
        raise PackageError(f"Pinned Xcode {base['versions']['xcode']} required; found {xcode}")
    if any(line[:1] in ("-", "+", "U") for line in git(checkout, "submodule", "status", "--recursive").decode().splitlines()):
        raise PackageError("Submodules are missing or differ from their pinned commits; initialize the documented dependencies")
    if not configuration or not configuration.is_file():
        raise PackageError("A local configuration JSON with your own app/API identifiers is required")
    validate_configuration(load_json(configuration))
    return base


def validate_configuration(config):
    """Validate the pinned resolver's complete input without echoing any value.

    The upstream resolver interpolates strings into Starlark; constrain the values
    here so a malformed local configuration cannot become executable build code.
    This is syntax validation, not proof that Telegram has issued the API credentials.
    """
    fields = {"bundle_id", "api_id", "api_hash", "team_id", "app_center_id",
              "is_internal_build", "is_appstore_build", "appstore_id",
              "app_specific_url_scheme", "premium_iap_product_id", "enable_siri", "enable_icloud"}
    if not isinstance(config, dict):
        raise PackageError("Build configuration must be a JSON object")
    missing, unknown = fields - config.keys(), config.keys() - fields
    if missing:
        raise PackageError("Missing build configuration fields: " + ", ".join(sorted(missing)))
    if unknown:
        raise PackageError("Build configuration contains unsupported fields; use the complete minimal example")
    api_id = config["api_id"]
    if isinstance(api_id, bool) or not isinstance(api_id, (str, int)) or not re.fullmatch(r"[0-9]+", str(api_id)):
        raise PackageError("api_id must be the owner's positive numeric Telegram API identifier")
    if len(str(api_id)) > 10 or not 0 < int(api_id) <= 2147483647:
        raise PackageError("api_id must fit a positive Int32")
    patterns = {
        "api_hash": r"[0-9A-Fa-f]{32}",
        "bundle_id": r"[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z][A-Za-z0-9-]*)+",
        "team_id": r"[A-Z0-9]{10}",
        "app_center_id": r"(?:0|[0-9A-Fa-f-]{36})",
        "appstore_id": r"[0-9]+",
        "app_specific_url_scheme": r"[a-z][a-z0-9+.-]*",
        "premium_iap_product_id": r"[A-Za-z0-9._-]*",
    }
    for key, pattern in patterns.items():
        if not isinstance(config[key], str) or not re.fullmatch(pattern, config[key]):
            raise PackageError(f"Invalid {key}; use the documented Simulator configuration format")
    for key in ("is_internal_build", "is_appstore_build"):
        if config[key] not in ("true", "false"):
            raise PackageError(f"{key} must be the string true or false")
    if config["is_appstore_build"] != "false":
        raise PackageError("This Simulator wrapper requires is_appstore_build to be false")
    for key in ("enable_siri", "enable_icloud"):
        if not isinstance(config[key], bool):
            raise PackageError(f"{key} must be a JSON boolean")
    if config["team_id"] == "DRAFTLOCAL" and (config["enable_siri"] or config["enable_icloud"]):
        raise PackageError("The Simulator-only DRAFTLOCAL marker requires Siri and iCloud disabled")


def make_command(checkout, config, cache, command):
    return [sys.executable, checkout / "build-system/Make/Make.py", "--cacheDir", cache,
            command, "--configurationPath", config, "--xcodeManagedCodesigning",
            "--disableProvisioningProfiles"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    boot = sub.add_parser("bootstrap", help="Create a new checkout at the exact pinned upstream commit")
    boot.add_argument("checkout", type=Path)
    boot.add_argument("--submodules", action="store_true", help="Initialize the pinned submodules (large download)")
    for command in ("apply", "check"):
        child = sub.add_parser(command, help="Apply and verify the package" if command == "apply" else "Reconstruct and verify in a clean checkout")
        child.add_argument("checkout", type=Path)
        child.add_argument("--with-demo", action="store_true")
        if command == "check":
            child.add_argument("--reference", type=Path, help="Compare every integrated file to the development checkout")
    exp = sub.add_parser("export", help="Export only explicitly allowlisted integration changes")
    exp.add_argument("checkout", type=Path)
    exp.add_argument("--paths", type=Path, default=ROOT / "Integration/export-paths.json")
    exp.add_argument("--invalidate-demo", action="store_true", help="Explicitly invalidate only generated demo manifest state after feature export preflight; retain all authored demo source/patch files")
    demo = sub.add_parser("export-demo", help="Seal the separately authored demo against an exact feature reference")
    demo.add_argument("--reference", type=Path, required=True)
    demo.add_argument("--paths", type=Path, default=ROOT / "Integration/demo-export-paths.json")
    verify = sub.add_parser("verify", help="Verify package source hashes and manifest inventory without an upstream checkout")
    verify.add_argument("--with-demo", action="store_true")
    build = sub.add_parser("build", help="Build the reconstructed iOS Simulator client through upstream Make.py")
    build.add_argument("checkout", type=Path)
    build.add_argument("--configuration", type=Path, required=True)
    build.add_argument("--cache", type=Path, required=True)
    build.add_argument("--build-number", type=int, required=True, help="Use a new build number for every installed build")
    build.add_argument("--with-demo", action="store_true")
    test = sub.add_parser("test", help="Run packaging tests; optionally run the real native feature targets")
    test.add_argument("--native", action="store_true")
    test.add_argument("--checkout", type=Path)
    test.add_argument("--configuration", type=Path)
    test.add_argument("--cache", type=Path)
    test.add_argument("--with-demo", action="store_true")
    args = parser.parse_args(argv)
    if hasattr(args, "checkout") and args.checkout and args.command != "bootstrap":
        args.checkout = args.checkout.resolve()
    if args.command == "bootstrap":
        bootstrap(ROOT, args.checkout, args.submodules)
    elif args.command in ("apply", "check"):
        apply(ROOT, args.checkout, args.with_demo, require_clean=args.command == "check")
        if args.command == "check" and args.reference:
            _, manifest = validate_package(ROOT, args.with_demo)
            for path, sha in all_expected(manifest, args.with_demo).items():
                check_hash(bounded(args.reference, path), sha)
            print("Reconstruction matches every development source hash.")
    elif args.command == "export":
        export(ROOT, args.checkout, args.paths, args.invalidate_demo)
    elif args.command == "export-demo":
        export_demo(ROOT, args.reference.resolve(), args.paths)
    elif args.command == "verify":
        validate_package(ROOT, args.with_demo)
        print("Manifest/source hashes verified. iOS build/runtime NOT RUN.")
    elif args.command == "build":
        toolchain(ROOT, args.checkout, args.configuration, args.with_demo)
        if args.build_number <= 0:
            raise PackageError("Build number must be positive")
        command = make_command(args.checkout, args.configuration.resolve(), args.cache.resolve(), "build")
        run(command + ["--buildNumber", args.build_number, "--configuration", "debug_sim_arm64"], cwd=args.checkout, capture=False)
    elif args.command == "test":
        suite = unittest.defaultTestLoader.discover(str(ROOT / "Tests"), pattern="test_*.py")
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful() or not result.testsRun:
            raise PackageError("Packaging tests failed or no tests ran")
        validate_package(ROOT, args.with_demo)
        if args.native:
            if not args.checkout or not args.cache:
                raise PackageError("Native tests require --checkout, --configuration and --cache")
            base = toolchain(ROOT, args.checkout, args.configuration, args.with_demo)
            targets = base.get("native_test_targets", [])
            if not targets:
                raise PackageError("No native feature test targets are registered in BASE.json")
            for entry in targets:
                target, selection = native_test_spec(entry)
                selection_args = ["--testFilter", selection] if selection else []
                run(make_command(args.checkout, args.configuration.resolve(), args.cache.resolve(), "test") + ["--target", target, *selection_args], cwd=args.checkout, capture=False)
        else:
            print("Packaging tests PASS. Native feature tests, iOS build, UI/runtime and capture NOT RUN.")


if __name__ == "__main__":
    try:
        main()
    except (PackageError, OSError, KeyError, UnicodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
