"""Launch diagnostics must not copy arbitrary app data or escape an owned device."""

import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import demo_diagnostics as diagnostics


class DemoDiagnosticsTests(unittest.TestCase):
    udid = "C4C2E100-B498-4737-B580-C74284A439B9"
    container_id = "13AA28E1-6456-496A-B6AF-6FA95A5702E1"

    def report(self):
        return {"schema_version": 1, "phases": [{"phase": "launchEntered", "utc": 1794500000.25}]}

    def tree(self, root):
        container = root / self.udid / "data/Containers/Data/Application" / self.container_id
        source = container / diagnostics.REPORT_PATH
        source.parent.mkdir(parents=True)
        return container, source

    def test_exports_original_bytes_only_from_owned_data_container(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            container, source = self.tree(root)
            content = json.dumps(self.report(), indent=2).encode() + b"\n"
            source.write_bytes(content)
            output = root / "evidence"
            output.mkdir()
            with mock.patch.object(diagnostics.package, "run", return_value=str(container).encode()) as run, \
                    mock.patch.object(diagnostics, "print") as printed:
                result = diagnostics.export_launch_report(self.udid, "org.fixture", root, output)
            run.assert_called_once_with(["xcrun", "simctl", "get_app_container", self.udid, "org.fixture", "data"], timeout=15)
            self.assertEqual(result["status"], "saved")
            self.assertEqual(result["count"], 1)
            self.assertEqual((output / result["path"]).read_bytes(), content)
            self.assertEqual(result["sha256"], diagnostics.hashlib.sha256(content).hexdigest())
            printed.assert_called_once()
            line = printed.call_args.args[0]
            self.assertTrue(line.startswith("DRAFT_POCKET_LAUNCH_DIAGNOSTICS "))
            self.assertEqual(line, "DRAFT_POCKET_LAUNCH_DIAGNOSTICS schema=1 phases=launchEntered@1794500000.25")
            self.assertNotIn("{", line)
            self.assertNotIn(str(container), line)

    def test_missing_file_is_reported_without_inventing_launch_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            container, _ = self.tree(root)
            with mock.patch.object(diagnostics.package, "run", return_value=str(container).encode()):
                result = diagnostics.export_launch_report(self.udid, "org.fixture", root, root)
            self.assertEqual(result["status"], "absent")
            self.assertFalse((root / "demo-launch-phases.json").exists())

    def test_exporter_accepts_exact_producer_vocabulary_and_rolling_operation_suffix(self):
        source = (Path(__file__).resolve().parents[1] /
                  "Demo/Overlay/submodules/TelegramCore/Sources/DraftPocketDemoBootstrap.swift").read_text(encoding="utf-8")
        body = re.search(r"public enum LaunchPhase: String, Codable \{(.*?)^    \}", source, re.S | re.M).group(1)
        declared = {name.strip() for line in re.findall(r"^        case (.+)$", body, re.M) for name in line.split(",")}
        self.assertEqual(declared, diagnostics.PHASES)
        item = {"schema_version": 1, "phases": [{"phase": name, "utc": 1794500000.25} for name in sorted(declared)]}
        self.assertEqual(diagnostics.validate_report(json.dumps(item).encode()), item)
        # Late QA diagnostics can contain only operation phases after the
        # producer has discarded earlier startup records from its rolling64.
        item["phases"] = [{"phase": "pocketStoreConflict", "utc": 1794500000.25}] * 64
        self.assertEqual(diagnostics.validate_report(json.dumps(item).encode()), item)

    def test_different_device_bundle_path_and_relative_path_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            container, _ = self.tree(root)
            for path in (str(container).replace(self.udid, self.container_id),
                         str(container).replace("Data", "Bundle"), "relative/path"):
                with self.subTest(path=path), self.assertRaises(diagnostics.package.PackageError):
                    diagnostics.owned_data_container(path, root, self.udid)

    def test_unknown_fields_and_arbitrary_phase_values_are_rejected(self):
        payloads = []
        for mutate in (lambda x: x.update(account="private"),
                       lambda x: x["phases"][0].update(message="private"),
                       lambda x: x["phases"][0].update(phase="account-private"),
                       lambda x: x.update(schema_version=True),
                       lambda x: x.update(phases=[]),
                       lambda x: x.update(phases=x["phases"] * 65)):
            item = self.report()
            mutate(item)
            payloads.append(item)
        for item in payloads:
            with self.subTest(item=item), self.assertRaises(diagnostics.package.PackageError):
                diagnostics.validate_report(json.dumps(item).encode())

    def test_invalid_timestamp_types_and_nonfinite_values_are_rejected(self):
        for value in (True, "1794500000", float("nan"), float("inf"), -1, 10 ** 1000):
            item = self.report()
            item["phases"][0]["utc"] = value
            with self.subTest(value_type=type(value)), self.assertRaises(diagnostics.package.PackageError):
                diagnostics.validate_report(json.dumps(item).encode())

    def test_oversized_duplicate_keys_and_malformed_json_are_rejected(self):
        for content in (b" " * (diagnostics.MAX_BYTES + 1), b"\xff", b"{", b"[]",
                        b'{"schema_version":1,"schema_version":1,"phases":[]}'):
            with self.subTest(size=len(content)), self.assertRaises(diagnostics.package.PackageError):
                diagnostics.validate_report(content)

    def test_invalid_content_is_never_copied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            container, source = self.tree(root)
            source.write_text('{"arbitrary":"private"}')
            with mock.patch.object(diagnostics.package, "run", return_value=str(container).encode()), \
                    mock.patch.object(diagnostics, "print") as printed, \
                    self.assertRaises(diagnostics.package.PackageError):
                diagnostics.export_launch_report(self.udid, "org.fixture", root, root)
            self.assertFalse((root / "demo-launch-phases.json").exists())
            printed.assert_not_called()

    def test_source_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            container, source = self.tree(root)
            external = root / "unrelated.json"
            external.write_text(json.dumps(self.report()))
            try:
                source.symlink_to(external)
            except OSError:
                self.skipTest("File symlink creation requires privileges on this host")
            with mock.patch.object(diagnostics.package, "run", return_value=str(container).encode()), \
                    self.assertRaises(diagnostics.package.PackageError):
                diagnostics.export_launch_report(self.udid, "org.fixture", root, root)
            self.assertFalse((root / "demo-launch-phases.json").exists())


if __name__ == "__main__":
    unittest.main()
