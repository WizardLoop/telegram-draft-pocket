"""Reject plausible-looking recordings that lack real scenario evidence."""

import importlib.util
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
SPEC = importlib.util.spec_from_file_location("capture_demo", Path(__file__).parents[1] / "scripts/capture-demo.py")
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


class CaptureEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.selection = "DraftPocketDemoUITests/testCaptureProductionStory"
        self.xml = '<testsuite><testcase classname="DraftPocketDemoUITests" name="testCaptureProductionStory"/></testsuite>'
        names = ["story_start", "put_aside_start", "put_aside_end", "put_aside_start", "put_aside_end",
                 "send_local_start", "send_local_end", "restore_start", "restore_end", "send_local_start", "send_local_end", "story_end"]
        self.events = [{"event": name, "utc": 101 + index} for index, name in enumerate(names)]

    def verify(self, xml=None, events=None):
        return capture.verify_scenario(self.xml if xml is None else xml,
                                       self.events if events is None else events, self.selection, 100, 120)

    def test_requested_scenario_and_ordered_recorded_actions(self):
        self.assertEqual(self.verify(), 1)

    def test_generic_success_or_wrong_test_does_not_prove_capture(self):
        for xml in ('<testsuite><testcase name="GenericSuccess"/></testsuite>',
                    self.xml.replace('testCaptureProductionStory', 'testUnrelatedAnimation'),
                    self.xml.replace('testCaptureProductionStory', 'testCaptureProductionStoryFake'),
                    self.xml.replace('</testsuite>', '<testcase classname="Other" name="testOther"/></testsuite>')):
            with self.subTest(xml=xml), self.assertRaises(capture.package.PackageError):
                self.verify(xml=xml)

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
        for child in ('<failure/>', '<error/>', '<skipped/>'):
            xml = self.xml.replace('/></testsuite>', '>' + child + '</testcase></testsuite>')
            with self.subTest(child=child), self.assertRaises(capture.package.PackageError):
                self.verify(xml=xml)


if __name__ == "__main__":
    unittest.main()
