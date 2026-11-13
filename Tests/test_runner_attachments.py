"""Exercise the pinned runner's actual failed-test export and exit-code path."""

import ast
import hashlib
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "Tests/fixtures/xctestrunner"
TARGET = "xctestrunner/test_runner/xcresult_util.py"
SOURCE_SHA256 = "996b0662137116cb05809afd46c2422f46bb33da15345c3b118119f1bd2f8891"


class RunnerAttachmentExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = (FIXTURES / "xcresult_util.py").read_text(encoding="utf-8")
        if hashlib.sha256(cls.original.encode()).hexdigest() != SOURCE_SHA256:
            raise AssertionError("The fixture must remain the exact pinned upstream source")
        patch = (ROOT / "Demo/Overlay/build-system/draft-pocket-xctestrunner.patch").read_text(encoding="utf-8")
        marker = f"diff --git a/{TARGET} b/{TARGET}\n"
        if patch.count(marker) != 1:
            raise AssertionError("Expected one optional runner export patch")
        section = marker + patch.split(marker, 1)[1].split("\ndiff --git ", 1)[0]
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            source = checkout / TARGET
            source.parent.mkdir(parents=True)
            source.write_bytes(cls.original.encode())
            subprocess.run(["git", "init", "--quiet"], cwd=checkout, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "--check", "-"],
                           input=section.encode(), cwd=checkout, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "-"],
                           input=section.encode(), cwd=checkout, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            cls.patched = source.read_text(encoding="utf-8")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = str(Path(self.temporary.name) / "export")
        self.activities = [{"attachments": {"_type": {"_name": "Array"}}}]
        leaf = {"testStatus": {"_value": "Failure"}, "summaryRef": {"id": {"_value": "failure"}}}
        self.objects = {
            None: {"actions": {"_values": [{"_type": {"_name": "ActionRecord"},
                                           "actionResult": {"testsRef": {"id": {"_value": "tests"}}}}]}},
            "tests": {"summaries": {"_values": [{"testableSummaries": {"_values": [{
                "tests": {"_values": [{"subtests": {"_values": [leaf]}}]}}]}}]}},
            "failure": {"identifier": {"_value": "DemoTests/testStory"},
                        "activitySummaries": {"_values": self.activities}},
        }
        self.export = mock.Mock()

    def runner(self, source=None):
        tree = ast.parse(self.patched if source is None else source)
        names = {"ExposeXcresult", "_ExposeDiagnostics", "_ExposeAttachments", "_GetFailureTestRefs"}
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        self.assertEqual({node.name for node in functions}, names)
        scope = {"os": os, "subprocess": SimpleNamespace(check_call=self.export),
                 "ios_errors": SimpleNamespace(XcresultError=RuntimeError),
                 "_GetResultBundleObject": lambda path, bundle_id=None: self.objects[bundle_id],
                 "_MakeXcresulttoolCommand": lambda args: ["xcrun", "xcresulttool", *args]}
        exec(compile(ast.Module(body=functions, type_ignores=[]), "pinned xcresult_util.py", "exec"), scope)
        return SimpleNamespace(**scope)

    def test_observed_empty_array_no_longer_masks_failed_test(self):
        with self.assertRaisesRegex(KeyError, "_values"):
            self.runner(self.original).ExposeXcresult("test.xcresult", self.output)
        self.runner().ExposeXcresult("test.xcresult", self.output)
        self.export.assert_not_called()

    def test_nonempty_attachment_is_still_exported_with_its_payload(self):
        self.activities.append({"attachments": {"_values": [{
            "filename": {"_value": "failure.png"}, "payloadRef": {"id": {"_value": "payload"}}
        }]}})
        self.runner().ExposeXcresult("test.xcresult", self.output)
        self.export.assert_called_once_with([
            "xcrun", "xcresulttool", "export", "--path", "test.xcresult", "--output-path",
            os.path.join(self.output, "Attachments", "DemoTests/testStory", "failure.png"),
            "--type", "file", "--id", "payload"])

    def test_malformed_nonempty_data_still_fails(self):
        for attachments, error in (({"_values": None}, TypeError),
                                   ({"_values": [{"filename": {"_value": "broken.png"}}]}, KeyError)):
            self.activities[:] = [{"attachments": attachments}]
            with self.subTest(attachments=attachments), self.assertRaises(error):
                self.runner().ExposeXcresult("test.xcresult", self.output)

    def test_original_failed_xctest_exit_code_is_returned_after_empty_export(self):
        scope = {"os": os, "subprocess": subprocess, "logging": logging, "shutil": shutil,
                 "xcresult_util": self.runner(),
                 "xcode_info_util": SimpleNamespace(GetXcodeVersionNumber=lambda: 2701)}
        exec(compile((FIXTURES / "session_run_test.py").read_text(encoding="utf-8"),
                     "pinned XctestSession.RunTest", "exec"), scope)
        owner = SimpleNamespace(_prepared=True, _xctestrun_obj=SimpleNamespace(Run=mock.Mock(return_value=1)),
                                _output_dir=self.output, _sdk="iphonesimulator", _startup_timeout_sec=150,
                                _destination_timeout_sec=30, _keep_xcresult_data=True)
        self.assertEqual(scope["RunTest"](owner, "OWNED-SIMULATOR"), 1)
        owner._xctestrun_obj.Run.assert_called_once()
        self.export.assert_not_called()


if __name__ == "__main__":
    unittest.main()
