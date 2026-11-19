"""Private emitted files must never dirty the source checkout or enter artifacts."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import package
from private_runners import PrivateRunners


class PrivateRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="draft-pocket-private-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.checkout, self.artifacts, self.parent = [self.root / name for name in ("checkout", "artifacts", "temporary")]
        for path in (self.checkout, self.artifacts, self.parent):
            path.mkdir()

    def storage(self, **options):
        return PrivateRunners(self.checkout, self.artifacts, temporary_parent=self.parent, **options)

    def test_real_git_inventory_stays_exact_across_two_takes_and_native_allocation(self):
        package.git(self.checkout, "init")
        package.git(self.checkout, "config", "user.name", "Private Runner Tests")
        package.git(self.checkout, "config", "user.email", "runner@example.invalid")
        package.git(self.checkout, "config", "core.autocrlf", "false")
        source = self.checkout / "Feature.swift"
        source.write_bytes(b"let enabled = false\n")
        package.git(self.checkout, "add", ".")
        package.git(self.checkout, "commit", "-m", "Base")
        source.write_bytes(b"let enabled = true\n")
        expected = {"Feature.swift": package.digest(source.read_bytes())}
        self.assertTrue(package.is_applied(self.checkout, expected))
        for take in ("story", "qa", "native"):
            storage = self.storage()
            try:
                script = storage.script_path(take + ".sh")
                script.write_text("private generated environment", encoding="utf-8")
                Path(str(script) + ".started").write_text("", encoding="utf-8")
                self.assertFalse(script.is_relative_to(self.checkout))
                self.assertFalse(script.is_relative_to(self.artifacts))
                self.assertTrue(package.is_applied(self.checkout, expected))
                self.assertEqual(package.changed_paths(self.checkout), set(expected))
            finally:
                storage.cleanup()
            self.assertFalse(storage.path.exists())
            self.assertTrue(package.is_applied(self.checkout, expected))
        # A user's runner-looking source file is still rejected by strict inventory.
        untracked = self.checkout / ".draft-pocket-runners"
        untracked.mkdir()
        (untracked / "user.sh").write_text("user file", encoding="utf-8")
        self.assertFalse(package.is_applied(self.checkout, expected))
        self.assertEqual((untracked / "user.sh").read_text(), "user file")

    def test_default_temp_inside_checkout_or_artifacts_rejects_before_allocation(self):
        for parent in (self.checkout, self.artifacts):
            with self.subTest(parent=parent), mock.patch("private_runners.tempfile.gettempdir", return_value=str(parent)), \
                    mock.patch("private_runners.tempfile.mkdtemp") as allocate, \
                    self.assertRaisesRegex(package.PackageError, "outside checkout and artifacts"):
                PrivateRunners(self.checkout, self.artifacts)
            allocate.assert_not_called()
            self.assertEqual(list(parent.iterdir()), [])

    def test_temp_alias_into_checkout_is_rejected_before_allocation(self):
        alias = self.root / "temp-alias"
        if os.name == "nt":
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                "New-Item -ItemType Junction -Path $env:DRAFT_PRIVATE_ALIAS -Target $env:DRAFT_PRIVATE_TARGET | Out-Null"],
                env={**os.environ, "DRAFT_PRIVATE_ALIAS": str(alias), "DRAFT_PRIVATE_TARGET": str(self.checkout)},
                check=True, capture_output=True)
        else:
            alias.symlink_to(self.checkout, target_is_directory=True)
        with mock.patch("private_runners.tempfile.mkdtemp") as allocate, self.assertRaises(package.PackageError):
            PrivateRunners(self.checkout, self.artifacts, temporary_parent=alias)
        allocate.assert_not_called()

    def test_failed_emission_partial_script_and_started_marker_are_cleaned_idempotently(self):
        storage = self.storage()
        script = storage.script_path("failed-emission.sh")
        script.write_bytes(b"partial private environment")
        Path(str(script) + ".started").write_bytes(b"")
        if os.name == "posix":
            self.assertEqual(storage.path.stat().st_mode & 0o777, 0o700)
        storage.cleanup()
        storage.cleanup()
        self.assertFalse(storage.path.exists())

    def test_replaced_directory_is_not_deleted(self):
        storage = self.storage()
        script = storage.script_path("test.sh")
        script.write_text("private", encoding="utf-8")
        original = storage.path.with_name(storage.path.name + "-original")
        storage.path.rename(original)
        storage.path.mkdir(mode=0o700)
        sentinel = storage.path / "test.sh"
        sentinel.write_text("unrelated", encoding="utf-8")
        with self.assertRaisesRegex(package.PackageError, "ownership/path changed"):
            storage.cleanup()
        self.assertEqual(sentinel.read_text(), "unrelated")
        self.assertTrue((original / "test.sh").exists())

    def test_unregistered_entry_prevents_any_deletion_and_paths_cannot_escape(self):
        storage = self.storage()
        script = storage.script_path("test.sh")
        script.write_text("private", encoding="utf-8")
        unknown = storage.path / "unexpected"
        unknown.mkdir()
        with self.assertRaisesRegex(package.PackageError, "unexpected entry"):
            storage.cleanup()
        self.assertTrue(script.exists())
        unknown.rmdir()
        for name in ("../escape.sh", "/absolute.sh", "nested/test.sh", "test.sh", "test.sh.started"):
            with self.subTest(name=name), self.assertRaises(package.PackageError):
                storage.script_path(name)
        storage.cleanup()


if __name__ == "__main__":
    unittest.main()
