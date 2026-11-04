"""Reject plausible-looking recordings that lack real scenario evidence."""

import importlib.util
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
SPEC = importlib.util.spec_from_file_location("capture_demo", Path(__file__).parents[1] / "scripts/capture-demo.py")
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


class CaptureEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.selection = "DraftPocketDemoUITests/testCaptureProductionStory"
        self.cases = [{"identifier": "DraftPocketDemoUITests/testCaptureProductionStory()", "status": "Success"}]
        names = ["story_start", "put_aside_start", "put_aside_end", "incoming_question_visible", "put_aside_start", "put_aside_end",
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

    def test_native_summary_reference_is_validated_and_retained(self):
        leaf = self.summary["summaries"]["_values"][0]["testableSummaries"]["_values"][0]["tests"]["_values"][0]["subtests"]["_values"][0]
        leaf["summaryRef"] = {"id": {"_value": "0~screenshot-summary"}}
        cases = capture.xcresult_cases(self.root, lambda _: self.summary)
        self.assertEqual(cases[0]["summary_ref"], "0~screenshot-summary")
        leaf["summaryRef"]["id"]["_value"] = "--wrong-reference"
        with self.assertRaises(capture.package.PackageError):
            capture.xcresult_cases(self.root, lambda _: self.summary)

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


class NamedScreenshotTests(unittest.TestCase):
    def setUp(self):
        self.selection = "DraftPocketDemoUITests/testCaptureProductionStory"
        self.case = {"identifier": self.selection + "()", "status": "Success", "summary_ref": "0~summary"}
        self.content = b"\x89PNG\r\n\x1a\nexample payload for mocked native exporter"
        self.attachments = []
        for name, count in capture.SCREENSHOTS["testCaptureProductionStory"].items():
            for _ in range(count):
                self.attachments.append({
                    "name": {"_value": name}, "uniformTypeIdentifier": {"_value": "public.png"},
                    "filename": {"_value": "../../never-use-native-filenames.png"},
                    "payloadRef": {"id": {"_value": f"0~payload-{len(self.attachments)}"}},
                    "payloadSize": {"_value": str(len(self.content))},
                    "timestamp": {"_value": "2026-11-05T10:00:00.000+0000"},
                })
        self.summary = {"identifier": {"_value": "testCaptureProductionStory()"}, "testStatus": {"_value": "Success"},
                        "activitySummaries": {"_values": [{"subactivities": {"_values": [
                            {"attachments": {"_values": self.attachments}}
                        ]}}]}}

    def test_only_explicit_names_are_selected_and_paths_are_generated(self):
        self.attachments.append({"name": {"_value": "private diagnostic screenshot"},
                                 "payloadRef": {"id": {"_value": "0~must-not-export"}}})
        selected = capture.named_screenshots(self.summary, self.selection)
        self.assertEqual(len(selected), 4)
        self.assertEqual(len(set(item["path"] for item in selected)), 4)
        self.assertTrue(all("/" not in item["path"] and ".." not in item["path"] for item in selected))
        self.assertNotIn("0~must-not-export", [item["payload_ref"] for item in selected])

    def test_wrong_case_missing_or_duplicate_named_images_rejected(self):
        variants = []
        for key, value in (("identifier", "testCaptureProductionStoryFake()"), ("testStatus", "Failure")):
            summary = copy.deepcopy(self.summary)
            summary[key]["_value"] = value
            variants.append(summary)
        for rows in (self.attachments[:-1], self.attachments + [self.attachments[0]]):
            summary = copy.deepcopy(self.summary)
            summary["activitySummaries"] = {"_values": [{"attachments": {"_values": rows}}]}
            variants.append(summary)
        for summary in variants:
            with self.subTest(summary=summary), self.assertRaises(capture.package.PackageError):
                capture.named_screenshots(summary, self.selection)

    def test_invalid_types_payload_refs_sizes_and_timestamps_rejected(self):
        for key, value in (("uniformTypeIdentifier", "public.plain-text"), ("payloadSize", "0"),
                           ("payloadSize", str(33 * 1024 * 1024)), ("payloadSize", 15),
                           ("timestamp", "2026-11-05T10:00:00"), ("timestamp", "invalid")):
            summary = copy.deepcopy(self.summary)
            summary["activitySummaries"]["_values"][0]["subactivities"]["_values"][0]["attachments"]["_values"][0][key] = {"_value": value}
            with self.subTest(key=key, value=value), self.assertRaises(capture.package.PackageError):
                capture.named_screenshots(summary, self.selection)
        self.attachments[0]["payloadRef"]["id"]["_value"] = "--output-path=/outside"
        with self.assertRaises(capture.package.PackageError):
            capture.named_screenshots(self.summary, self.selection)

    def export_tool(self, command):
        if command[0] == "ffprobe":
            return json.dumps({"streams": [{"codec_type": "video", "codec_name": "png", "nb_read_frames": "1",
                                            "width": 1206, "height": 2622}]}).encode()
        self.assertEqual(command[:7], ["xcrun", "xcresulttool", "export", "object", "--legacy", "--type", "file"])
        Path(command[command.index("--output-path") + 1]).write_bytes(self.content)
        return b""

    def test_export_preserves_bytes_and_native_identity_without_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = Path(temporary)
            with mock.patch.object(capture.package, "run", side_effect=self.export_tool) as run:
                capture.export_named_screenshots(Path("test.xcresult"), artifacts, self.case, self.selection, self.summary)
            self.assertEqual(run.call_count, 8)
            manifest = json.loads((artifacts / "screenshots/manifest.json").read_text())
            self.assertEqual(manifest["native_case"], self.selection)
            self.assertEqual(manifest["summary_ref"], "0~summary")
            self.assertEqual(len(manifest["screenshots"]), 4)
            for item in manifest["screenshots"]:
                self.assertEqual((artifacts / "screenshots" / item["path"]).read_bytes(), self.content)
                self.assertEqual(item["sha256"], hashlib.sha256(self.content).hexdigest())
                self.assertEqual((item["width"], item["height"]), (1206, 2622))
            self.assertEqual(len(list(artifacts.rglob("*"))), 6)

    def test_invalid_export_fails_before_artifact_promotion(self):
        for invalid in (b"not PNG", b"\x89PNG\r\n\x1a\nwrong length"):
            def tool(command):
                Path(command[command.index("--output-path") + 1]).write_bytes(invalid)
                return b""
            with self.subTest(payload=invalid), tempfile.TemporaryDirectory() as temporary:
                artifacts = Path(temporary)
                with mock.patch.object(capture.package, "run", side_effect=tool), self.assertRaises(capture.package.PackageError):
                    capture.export_named_screenshots(Path("test.xcresult"), artifacts, self.case, self.selection, self.summary)
                self.assertEqual(list(artifacts.iterdir()), [])

    def test_non_image_or_multiframe_export_is_not_promoted(self):
        def tool(command):
            if command[0] == "ffprobe":
                return json.dumps({"streams": [{"codec_type": "video", "codec_name": "png", "nb_read_frames": "2",
                                                "width": 1206, "height": 2622}]}).encode()
            return self.export_tool(command)
        with tempfile.TemporaryDirectory() as temporary:
            artifacts = Path(temporary)
            with mock.patch.object(capture.package, "run", side_effect=tool), self.assertRaises(capture.package.PackageError):
                capture.export_named_screenshots(Path("test.xcresult"), artifacts, self.case, self.selection, self.summary)
            self.assertEqual(list(artifacts.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
