"""Exercise packaging against actual isolated Git repositories, not mocked exit codes."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


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
        (self.integration / self.old).write_text("let old = true\n", encoding="utf-8")
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
        (self.integration / self.old).write_text("let old = false\n", encoding="utf-8")
        (self.integration / self.new).write_text("// A new untracked production source.\n", encoding="utf-8")
        self.target = Path(self.temporary.name) / "clean target"
        package.run(["git", "clone", "--quiet", "--no-hardlinks", self.integration, self.target])
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


if __name__ == "__main__":
    unittest.main()
