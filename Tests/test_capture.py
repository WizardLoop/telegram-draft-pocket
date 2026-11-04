"""Reject plausible-looking recordings that lack real scenario evidence."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
SPEC = importlib.util.spec_from_file_location("capture_demo", Path(__file__).parents[1] / "scripts/capture-demo.py")
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


class CaptureEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.selection = "DraftPocketDemoUITests/testCaptureProductionStory"
        self.cases = [{"identifier": "DraftPocketDemoUITests/testCaptureProductionStory()", "status": "Success"}]
        names = ["story_start", "put_aside_start", "put_aside_end", "put_aside_start", "put_aside_end",
                 "send_local_start", "send_local_end", "restore_start", "restore_end", "send_local_start", "send_local_end", "story_end"]
        self.events = [{"event": name, "utc": 101 + index} for index, name in enumerate(names)]

    def verify(self, cases=None, events=None):
        return capture.verify_scenario(self.cases if cases is None else cases,
                                       self.events if events is None else events, self.selection, 100, 120)

    def test_requested_scenario_and_ordered_recorded_actions(self):
        self.assertEqual(self.verify(), 1)

    def test_generic_success_or_wrong_test_does_not_prove_capture(self):
        for cases in ('<testsuite><testcase name="GenericSuccess"/></testsuite>', [],
                      [{"identifier": "testUnrelatedAnimation", "status": "Success"}],
                      [{"identifier": "DraftPocketDemoUITests/testCaptureProductionStoryFake", "status": "Success"}],
                      self.cases + [{"identifier": "Other/testOther", "status": "Success"}]):
            with self.subTest(cases=cases), self.assertRaises(capture.package.PackageError):
                self.verify(cases=cases)

    def test_missing_or_reordered_actions_rejected(self):
        for events in ([], self.events[:-1], list(reversed(self.events)), [e for e in self.events if e["event"] != "restore_start"]):
            with self.subTest(events=events), self.assertRaises(capture.package.PackageError):
                self.verify(events=events)

    def test_invalid_or_outside_take_timestamps_rejected(self):
        for instant in (float("nan"), float("inf"), True, "103", 95, 125):
            events = [dict(e) for e in self.events]
            events[3]["utc"] = instant
            with self.subTest(instant=instant), self.assertRaises(capture.package.PackageError):
                self.verify(events=events)

    def test_failed_or_skipped_expected_case_rejected(self):
        for status in ("Failure", "Error", "Skipped", "Expected Failure", "Unknown"):
            cases = [dict(self.cases[0], status=status)]
            with self.subTest(status=status), self.assertRaises(capture.package.PackageError):
                self.verify(cases=cases)


class NativeResultTests(unittest.TestCase):
    def setUp(self):
        self.root = {"actions": {"_values": [{"_type": {"_name": "ActionRecord"},
                     "actionResult": {"testsRef": {"id": {"_value": "0~native-reference"}}}}]}}
        leaf = {"identifier": {"_value": "testCaptureProductionStory()"}, "testStatus": {"_value": "Success"}}
        group = {"identifier": {"_value": "DraftPocketDemoUITests"}, "subtests": {"_values": [leaf]}}
        self.summary = {"summaries": {"_values": [{"testableSummaries": {"_values": [{"tests": {"_values": [group]}}]}}]}}

    def test_native_schema_preserves_parent_class_and_leaf_status(self):
        seen = []
        def read(identifier):
            seen.append(identifier)
            return self.summary
        self.assertEqual(capture.xcresult_cases(self.root, read),
                         [{"identifier": "DraftPocketDemoUITests/testCaptureProductionStory()", "status": "Success"}])
        self.assertEqual(seen, ["0~native-reference"])

    def test_missing_wrong_or_ambiguous_native_schema_rejected(self):
        for root in ({}, {"actions": None}, {"actions": {"_values": [None]}},
                     {"actions": {"_values": self.root["actions"]["_values"] * 2}}):
            with self.subTest(root=root), self.assertRaises(capture.package.PackageError):
                capture.xcresult_cases(root, lambda _: self.summary)
        with self.assertRaises(capture.package.PackageError):
            capture.xcresult_cases(self.root, lambda _: {"summaries": []})

    def test_native_archive_copy_is_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outputs, destination = root / "outputs", root / "copy"
            outputs.mkdir()
            destination.mkdir()
            with zipfile.ZipFile(outputs / "outputs.zip", "w") as zipped:
                zipped.writestr("test.xcresult/Info.plist", b"native fixture bytes")
                zipped.writestr("unrelated.log", b"not copied")
            result = capture.copy_xcresult(outputs, destination)
            self.assertEqual((result / "Info.plist").read_bytes(), b"native fixture bytes")
            self.assertFalse((destination / "unrelated.log").exists())

    def test_archive_traversal_or_link_fails_before_extracting(self):
        for bad in ("test.xcresult/../escape", "/absolute", "test.xcresult/link"):
            with self.subTest(path=bad), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                outputs, destination = root / "outputs", root / "copy"
                outputs.mkdir()
                destination.mkdir()
                with zipfile.ZipFile(outputs / "outputs.zip", "w") as zipped:
                    zipped.writestr("test.xcresult/Info.plist", b"valid but must not be copied")
                    entry = zipfile.ZipInfo(bad)
                    if bad.endswith("link"):
                        entry.external_attr = (0o120777 << 16)
                    zipped.writestr(entry, b"outside")
                with self.assertRaises(capture.package.PackageError):
                    capture.copy_xcresult(outputs, destination)
                self.assertEqual(list(destination.iterdir()), [])

    def test_missing_or_ambiguous_native_bundle_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outputs, destination = root / "outputs", root / "copy"
            outputs.mkdir()
            destination.mkdir()
            with self.assertRaises(capture.package.PackageError):
                capture.copy_xcresult(outputs, destination)
            (outputs / "test.xcresult").mkdir()
            (outputs / "outputs.zip").write_bytes(b"not a valid archive")
            with self.assertRaisesRegex(capture.package.PackageError, "Ambiguous"):
                capture.copy_xcresult(outputs, destination)


if __name__ == "__main__":
    unittest.main()
