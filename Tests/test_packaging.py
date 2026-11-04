"""Exercise packaging against actual isolated Git repositories, not mocked exit codes."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("package", Path(__file__).parents[1] / "scripts/package.py")
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="draft-pocket-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "feature package"
        self.root.mkdir()
        (self.root / "Integration").mkdir()
        self.integration = Path(self.temporary.name) / "integration with spaces"
        self.integration.mkdir()
        package.git(self.integration, "init")
        package.git(self.integration, "config", "user.name", "Packaging Tests")
        package.git(self.integration, "config", "user.email", "packaging@example.invalid")
        package.git(self.integration, "config", "core.autocrlf", "false")
        (self.integration / "submodules/Example").mkdir(parents=True)
        self.old = "submodules/Example/Existing.swift"
        self.new = "submodules/Example/DraftPocket.swift"
        (self.integration / self.old).write_text("let old = true\n", encoding="utf-8", newline="\n")
        package.git(self.integration, "add", ".")
        package.git(self.integration, "commit", "-m", "Base fixture")
        self.sha = package.git(self.integration, "rev-parse", "HEAD").decode().strip()
        self.write_json("Integration/BASE.json", {
            "schema_version": 1,
            "upstream_url": "https://github.com/TelegramMessenger/Telegram-iOS.git",
            "commit": self.sha,
            "versions": {"xcode": "27.1"}
        })
        self.write_json("Integration/export-paths.json", {
            "schema_version": 1, "feature_new": [self.new], "feature_modified": [self.old]
        })
        (self.integration / self.old).write_text("let old = false\n", encoding="utf-8", newline="\n")
        (self.integration / self.new).write_text("// A new untracked production source.\n", encoding="utf-8", newline="\n")
        self.target = Path(self.temporary.name) / "clean target"
        package.run(["git", "-c", "core.autocrlf=false", "clone", "--quiet", "--no-hardlinks", self.integration, self.target])
        package.git(self.target, "config", "core.autocrlf", "false")
        package.export(self.root, self.integration, self.root / "Integration/export-paths.json")

    def write_json(self, path, value):
        (self.root / path).write_text(json.dumps(value), encoding="utf-8")

    def manifest(self):
        return package.load_json(self.root / "Integration/files.json")

    def target_snapshot(self):
        return {path.relative_to(self.target).as_posix(): path.read_bytes()
                for path in self.target.rglob("*") if path.is_file() and ".git" not in path.parts}

    def assert_preflight_rejects_unchanged(self, with_demo=False):
        before = self.target_snapshot()
        with self.assertRaises(package.PackageError):
            package.apply(self.root, self.target, with_demo)
        self.assertEqual(before, self.target_snapshot())

    def test_export_untracked_and_reconstruct_exact_hashes(self):
        package.apply(self.root, self.target, require_clean=True)
        self.assertEqual((self.target / self.old).read_bytes(), (self.integration / self.old).read_bytes())
        self.assertEqual((self.target / self.new).read_bytes(), (self.integration / self.new).read_bytes())
        before = self.target_snapshot()
        package.apply(self.root, self.target)
        self.assertEqual(before, self.target_snapshot())

    def test_wrong_base_rejected_before_writes(self):
        base = package.load_json(self.root / "Integration/BASE.json")
        base["commit"] = "a" * 40
        self.write_json("Integration/BASE.json", base)
        manifest = self.manifest()
        manifest["base_commit"] = "a" * 40
        self.write_json("Integration/files.json", manifest)
        self.assert_preflight_rejects_unchanged()

    def test_new_file_collision_rejected(self):
        (self.target / self.new).write_text("User content", encoding="utf-8")
        self.assert_preflight_rejects_unchanged()

    def test_partial_application_not_reported_as_applied(self):
        package.apply(self.root, self.target)
        (self.target / self.old).write_text("A user's later edit\n", encoding="utf-8")
        self.assert_preflight_rejects_unchanged()

    def test_tampered_overlay_rejected(self):
        (self.root / self.new).write_text("Not the declared source", encoding="utf-8")
        self.assert_preflight_rejects_unchanged()

    def test_before_hash_rejected(self):
        manifest = self.manifest()
        manifest["feature"]["patch"]["files"][0]["before_sha256"] = "a" * 64
        self.write_json("Integration/files.json", manifest)
        self.assert_preflight_rejects_unchanged()

    def test_after_hash_rejected_before_real_patch(self):
        manifest = self.manifest()
        manifest["feature"]["patch"]["files"][0]["after_sha256"] = "a" * 64
        self.write_json("Integration/files.json", manifest)
        self.assert_preflight_rejects_unchanged()

    def test_traversal_and_git_paths_rejected(self):
        for path in ("../outside", "/absolute", "submodules/../../outside", ".git/config",
                     "submodules/.GIT/config", "C:/Windows/file", "submodules\\file", "a//b",
                     ".git./config", ".git /config", "submodules/NUL", "submodules/x."):
            with self.subTest(path=path), self.assertRaises(package.PackageError):
                package.bounded(self.target, path)

    def test_symlink_component_rejected(self):
        link = self.target / "link"
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Creating symlinks is not permitted on this host; path guards still tested")
        with self.assertRaises(package.PackageError):
            package.bounded(self.target, "link/escaped.swift")

    def test_unlisted_overlay_rejected(self):
        (self.root / "submodules/Example/Unlisted.swift").write_text("extra", encoding="utf-8")
        self.assert_preflight_rejects_unchanged()

    def test_export_rejects_unlisted_source(self):
        (self.integration / "submodules/Example/Unlisted.swift").write_text("extra", encoding="utf-8")
        before = (self.root / "Integration/files.json").read_bytes()
        with self.assertRaises(package.PackageError):
            package.export(self.root, self.integration, self.root / "Integration/export-paths.json")
        self.assertEqual(before, (self.root / "Integration/files.json").read_bytes())

    def test_manifest_patch_inventory_rejected(self):
        manifest = self.manifest()
        manifest["feature"]["patch"]["files"][0]["target"] = "submodules/Example/Unexpected.swift"
        self.write_json("Integration/files.json", manifest)
        self.assert_preflight_rejects_unchanged()

    def test_demo_unavailable_rejected_before_mutation(self):
        self.assert_preflight_rejects_unchanged(with_demo=True)

    def test_staged_work_preserved(self):
        (self.target / self.old).write_text("User edit", encoding="utf-8")
        package.git(self.target, "add", self.old)
        staged = package.git(self.target, "diff", "--cached")
        self.assert_preflight_rejects_unchanged()
        self.assertEqual(staged, package.git(self.target, "diff", "--cached"))

    def test_clean_check_refuses_already_applied_tree(self):
        package.apply(self.root, self.target)
        with self.assertRaises(package.PackageError):
            package.apply(self.root, self.target, require_clean=True)

    def test_target_must_be_checkout_root(self):
        before = self.target_snapshot()
        with self.assertRaises(package.PackageError):
            package.apply(self.root, self.target / "submodules")
        self.assertEqual(before, self.target_snapshot())

    def test_case_colliding_targets_rejected(self):
        manifest = self.manifest()
        second = dict(manifest["feature"]["files"][0])
        second["source"] = second["target"] = self.new.lower()
        manifest["feature"]["files"].append(second)
        self.write_json("Integration/files.json", manifest)
        with self.assertRaisesRegex(package.PackageError, "case-insensitive"):
            package.validate_package(self.root)

    def test_unlisted_patch_rejected_before_mutation(self):
        manifest = self.manifest()
        del manifest["feature"]["patch"]
        self.write_json("Integration/files.json", manifest)
        self.assert_preflight_rejects_unchanged()

    def test_global_autocrlf_cannot_change_package_bytes(self):
        global_config = Path(self.temporary.name) / "global-gitconfig"
        global_config.write_text("[core]\n\tautocrlf = true\n", encoding="utf-8")
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(global_config), "GIT_CONFIG_NOSYSTEM": "1"}):
            package.apply(self.root, self.target, require_clean=True)
        self.assertEqual((self.target / self.old).read_bytes(), (self.integration / self.old).read_bytes())
        self.assertEqual((self.target / self.new).read_bytes(), (self.integration / self.new).read_bytes())

    def test_inherited_git_directory_cannot_redirect_install(self):
        original = (self.integration / self.old).read_bytes()
        with patch.dict(os.environ, {"GIT_DIR": str(self.integration / ".git"), "GIT_WORK_TREE": str(self.integration)}):
            package.apply(self.root, self.target, require_clean=True)
        self.assertEqual(original, (self.integration / self.old).read_bytes())
        self.assertEqual((self.target / self.old).read_bytes(), original)

    def author_demo(self):
        demo_new = "Tests/DraftPocketDemo/Fixture.swift"
        source = self.root / "Demo/Overlay" / demo_new
        source.parent.mkdir(parents=True)
        source.write_text("// Isolated fictional fixture.\n", encoding="utf-8", newline="\n")
        patch_text = (f"diff --git a/{self.old} b/{self.old}\n--- a/{self.old}\n+++ b/{self.old}\n"
                      "@@ -1 +1 @@\n-let old = false\n+let old = false // offline fixture\n")
        (self.root / "Integration/demo.patch").write_text(patch_text, encoding="utf-8", newline="\n")
        paths = {"schema_version": 1, "demo_new": [demo_new], "demo_modified": [self.old],
                 "feature_sha256": {self.old: package.digest((self.integration / self.old).read_bytes())}}
        self.write_json("Integration/demo-export-paths.json", paths)
        return paths

    def test_demo_export_and_ordered_clean_reconstruction(self):
        self.author_demo()
        package.export_demo(self.root, self.integration, self.root / "Integration/demo-export-paths.json")
        package.apply(self.root, self.target, with_demo=True, require_clean=True)
        self.assertEqual((self.target / self.old).read_text(), "let old = false // offline fixture\n")
        self.assertTrue((self.target / "Tests/DraftPocketDemo/Fixture.swift").is_file())
        package.apply(self.root, self.target, with_demo=True)
        with self.assertRaises(package.PackageError):
            package.apply(self.root, self.target, with_demo=False)

    def test_demo_stale_feature_input_rejected_without_manifest_write(self):
        paths = self.author_demo()
        paths["feature_sha256"][self.old] = "a" * 64
        self.write_json("Integration/demo-export-paths.json", paths)
        before = (self.root / "Integration/files.json").read_bytes()
        with self.assertRaisesRegex(package.PackageError, "stale feature input"):
            package.export_demo(self.root, self.integration, self.root / "Integration/demo-export-paths.json")
        self.assertEqual(before, (self.root / "Integration/files.json").read_bytes())

    def test_demo_can_extend_only_the_complete_feature_state(self):
        self.author_demo()
        package.export_demo(self.root, self.integration, self.root / "Integration/demo-export-paths.json")
        package.apply(self.root, self.target)
        package.apply(self.root, self.target, with_demo=True)
        self.assertEqual((self.target / self.old).read_text(), "let old = false // offline fixture\n")

    def test_demo_refuses_partial_feature_before_writes(self):
        self.author_demo()
        package.export_demo(self.root, self.integration, self.root / "Integration/demo-export-paths.json")
        package.apply(self.root, self.target)
        (self.target / self.new).write_text("Changed after feature install", encoding="utf-8")
        self.assert_preflight_rejects_unchanged(with_demo=True)

    def test_demo_export_rejects_unlisted_overlay_without_manifest_write(self):
        self.author_demo()
        (self.root / "Demo/Overlay/Unlisted.swift").write_text("unlisted", encoding="utf-8")
        before = (self.root / "Integration/files.json").read_bytes()
        with self.assertRaisesRegex(package.PackageError, "inventory"):
            package.export_demo(self.root, self.integration, self.root / "Integration/demo-export-paths.json")
        self.assertEqual(before, (self.root / "Integration/files.json").read_bytes())

    def test_explicit_native_test_overlay_excludes_packaging_tests(self):
        native = "Tests/ComposerHarness/Tests/HarnessDraftPocketSelectionTests.swift"
        path = self.integration / native
        path.parent.mkdir(parents=True)
        path.write_text("// Actual native test source fixture.\n", encoding="utf-8", newline="\n")
        paths = package.load_json(self.root / "Integration/export-paths.json")
        paths["feature_new"].append(native)
        self.write_json("Integration/export-paths.json", paths)
        (self.root / "Tests").mkdir()
        (self.root / "Tests/test_not_an_overlay.py").write_text("# Packaging test only.\n", encoding="utf-8")
        package.export(self.root, self.integration, self.root / "Integration/export-paths.json")
        package.apply(self.root, self.target, require_clean=True)
        self.assertEqual(path.read_bytes(), (self.target / native).read_bytes())
        self.assertFalse((self.target / "Tests/test_not_an_overlay.py").exists())

    def test_feature_reexport_needs_explicit_demo_invalidation(self):
        self.author_demo()
        package.export_demo(self.root, self.integration, self.root / "Integration/demo-export-paths.json")
        before_manifest = (self.root / "Integration/files.json").read_bytes()
        before_patch = (self.root / "Integration/demo.patch").read_bytes()
        before_source = (self.root / "Demo/Overlay/Tests/DraftPocketDemo/Fixture.swift").read_bytes()
        (self.integration / self.old).write_text("let old = false // feature fix\n", encoding="utf-8", newline="\n")
        with self.assertRaises(package.PackageError):
            package.export(self.root, self.integration, self.root / "Integration/export-paths.json")
        self.assertEqual(before_manifest, (self.root / "Integration/files.json").read_bytes())
        package.export(self.root, self.integration, self.root / "Integration/export-paths.json", invalidate_demo=True)
        self.assertFalse(self.manifest()["demo"]["available"])
        self.assertEqual(before_patch, (self.root / "Integration/demo.patch").read_bytes())
        self.assertEqual(before_source, (self.root / "Demo/Overlay/Tests/DraftPocketDemo/Fixture.swift").read_bytes())
        with self.assertRaisesRegex(package.PackageError, "stale feature input"):
            package.export_demo(self.root, self.integration, self.root / "Integration/demo-export-paths.json")
        self.assert_preflight_rejects_unchanged(with_demo=True)

    def test_export_rejects_autocrlf_patch_source_disagreement_before_any_write(self):
        before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        global_config = Path(self.temporary.name) / "global-export-gitconfig"
        global_config.write_text("[core]\n\tautocrlf = true\n", encoding="utf-8", newline="\n")
        package.git(self.integration, "config", "--unset", "core.autocrlf")
        (self.integration / self.old).write_bytes(b"let old = false // changed\r\n")
        (self.integration / self.new).write_bytes(b"// New payload must not be exported on failure.\n")
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(global_config), "GIT_CONFIG_NOSYSTEM": "1"}):
            with self.assertRaisesRegex(package.PackageError, "roundtrip differs.*normalize"):
                package.export(self.root, self.integration, self.root / "Integration/export-paths.json")
        after = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
