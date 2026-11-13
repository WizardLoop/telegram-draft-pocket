"""Optional crash evidence stays bound to the actual isolated application and take."""

import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import demo_crash_report as crash


class DemoCrashReportTests(unittest.TestCase):
    udid = "C4C2E100-B498-4737-B580-C74284A439B9"
    container_id = "13AA28E1-6456-496A-B6AF-6FA95A5702E1"
    bundle = "org.wizardloop.draftpocket"
    start = 1794530100.0
    end = 1794530200.0

    def timestamp(self, epoch):
        return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f %z")

    def fixture(self):
        metadata = {"bug_type": "309", "app_name": "Telegram", "bundleID": self.bundle,
                    "crashReporterKey": "PRIVATE_METADATA"}
        report = {
            "procName": "Telegram", "bundleInfo": {"CFBundleIdentifier": self.bundle},
            "procPath": f"/Users/USER/Library/Developer/CoreSimulator/Devices/{self.udid}/data/Containers/Bundle/Application/{self.container_id}/Telegram.app/Telegram",
            "procLaunch": self.timestamp(self.start + 10), "captureTime": self.timestamp(self.start + 30),
            "exception": {"type": "EXC_BREAKPOINT", "signal": "SIGTRAP", "message": "PRIVATE_EXCEPTION"},
            "termination": {"namespace": "SIGNAL", "code": 5, "indicator": "PRIVATE_REASON"},
            "asi": {"application": ["PRIVATE_LOG"]}, "faultingThread": 0,
            "threads": [{"queue": "PRIVATE_QUEUE", "threadState": {"secret": "PRIVATE_REGISTER"},
                         "frames": [{"symbol": "MetalEngine.init()", "imageOffset": 128, "symbolLocation": 24,
                                     "sourceFile": "/PRIVATE_SOURCE_PATH"}]}],
            "usedImages": [{"path": "/PRIVATE_IMAGE_PATH"}],
        }
        return metadata, report

    def encode(self, metadata, report):
        return json.dumps(metadata).encode() + b"\n" + json.dumps(report).encode()

    def summarize(self, metadata, report):
        return crash.summarize_report(self.encode(metadata, report), self.udid, self.bundle, "Telegram", self.start, self.end)

    def test_exact_owned_report_exports_only_whitelisted_fields(self):
        result = self.summarize(*self.fixture())
        self.assertEqual(result, {
            "launch_utc": self.start + 10, "crash_utc": self.start + 30,
            "exception": {"type": "EXC_BREAKPOINT", "signal": "SIGTRAP"},
            "termination": {"namespace": "SIGNAL", "code": 5},
            "frames": [{"symbol": "MetalEngine.init()", "image_offset": 128, "symbol_offset": 24}],
        })
        encoded = json.dumps(result)
        for private in ("PRIVATE", "Users", self.bundle, self.udid, self.container_id):
            self.assertNotIn(private, encoded)

    def test_both_metadata_and_body_bundle_and_process_must_match(self):
        edits = [lambda m, r: m.update(bundleID="org.other"),
                 lambda m, r: r["bundleInfo"].update(CFBundleIdentifier="org.other"),
                 lambda m, r: m.update(app_name="Other"),
                 lambda m, r: r.update(procName="Other"),
                 lambda m, r: m.update(name="Other"),
                 lambda m, r: m.pop("app_name"),
                 lambda m, r: m.update(bug_type="288")]
        for edit in edits:
            with self.subTest(edit=edit):
                metadata, report = self.fixture()
                edit(metadata, report)
                self.assertIsNone(self.summarize(metadata, report))

    def test_documented_metadata_name_is_supported_without_app_name(self):
        metadata, report = self.fixture()
        metadata["name"] = metadata.pop("app_name")
        self.assertIsNotNone(self.summarize(metadata, report))

    def test_full_owned_device_and_app_path_suffix_is_required(self):
        metadata, report = self.fixture()
        original = report["procPath"]
        wrong = [original.replace(self.udid, self.container_id), original.replace(self.udid, self.udid.lower()),
                 original.replace("Bundle", "Data"), original.replace("Telegram.app", "Other.app"),
                 original + "/extra", original.replace("/CoreSimulator/", "/../CoreSimulator/"),
                 original.lstrip("/"), original.replace(self.container_id, "not-a-uuid")]
        for value in wrong:
            with self.subTest(path=value):
                report["procPath"] = value
                self.assertIsNone(self.summarize(metadata, report))

    def test_user_prefix_redaction_does_not_weaken_device_suffix(self):
        metadata, report = self.fixture()
        for prefix in ("/Users/USER/Library/Developer", "/private/var/example/Library/Developer"):
            report["procPath"] = prefix + "/CoreSimulator/Devices/" + self.udid + f"/data/Containers/Bundle/Application/{self.container_id}/Telegram.app/Telegram"
            self.assertIsNotNone(self.summarize(metadata, report))

    def test_both_launch_and_crash_must_be_ordered_inside_take(self):
        for launch, end in ((self.start - 1, self.start + 30), (self.start + 20, self.start + 10),
                            (self.start + 10, self.end + 1)):
            metadata, report = self.fixture()
            report.update(procLaunch=self.timestamp(launch), captureTime=self.timestamp(end))
            self.assertIsNone(self.summarize(metadata, report))
        metadata, report = self.fixture()
        report["captureTime"] = "2026-11-13T00:35:37"
        self.assertIsNone(self.summarize(metadata, report))

    def test_nonfatal_simulated_and_malformed_reports_are_unavailable(self):
        for key in ("isNonFatal", "isSimulated"):
            metadata, report = self.fixture()
            report[key] = True
            self.assertIsNone(self.summarize(metadata, report))
        for data in (b"", b"{}", b"{}\n[]", b"\xff\n{}", b"{}\n{" * 30,
                     b'{"bug_type":"309","bug_type":"309"}\n{}', b" " * (crash.MAX_BYTES + 1)):
            self.assertIsNone(crash.summarize_report(data, self.udid, self.bundle, "Telegram", self.start, self.end))

    def test_only_exception_tokens_numeric_codes_and_bounded_function_symbols_survive(self):
        metadata, report = self.fixture()
        report["exception"]["signal"] = "SIGKILL (PRIVATE_REASON)"
        report["termination"].update(code=True, namespace="PRIVATE REASON")
        report["threads"][0]["frames"] = [
            {"symbol": "/Users/private/name.swift", "imageOffset": 12, "symbolLocation": True},
            {"symbol": "invalid\nPRIVATE_LOG", "imageOffset": -1},
        ] + [{"symbol": "validFunction()", "imageOffset": 32}] * 100
        result = self.summarize(metadata, report)
        self.assertEqual(result["exception"]["signal"], "SIGKILL")
        self.assertNotIn("termination", result)
        self.assertEqual(result["frames"][0], {"image_offset": 12})
        self.assertLessEqual(len(result["frames"]), crash.MAX_FRAMES)
        self.assertNotIn("PRIVATE", json.dumps(result))

    def write_report(self, directory, name="Telegram.ips", metadata=None, report=None):
        if metadata is None:
            metadata, report = self.fixture()
        target = directory / name
        target.write_bytes(self.encode(metadata, report))
        os.utime(target, (self.start + 30, self.start + 30))
        return target

    def export(self, reports, output):
        return crash.export_crash_summaries(self.udid, self.bundle, self.start, self.end, output,
                                            reports_directory=reports)

    def test_host_scan_saves_summary_never_raw_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            reports, output = root / "reports", root / "output"
            reports.mkdir()
            output.mkdir()
            raw = self.write_report(reports)
            result = self.export(reports, output)
            self.assertEqual(result["status"], "saved")
            self.assertEqual(result["count"], 1)
            self.assertEqual([p.name for p in output.iterdir()], [crash.OUTPUT_NAME])
            summary = json.loads((output / crash.OUTPUT_NAME).read_text())
            self.assertTrue(summary["diagnostic_only"])
            self.assertEqual(summary["reports"], [self.summarize(*self.fixture())])
            self.assertNotEqual(raw.read_bytes(), (output / crash.OUTPUT_NAME).read_bytes())

    def test_absent_or_nonmatching_report_never_means_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.assertEqual(self.export(root / "absent", root)["status"], "unavailable")
            metadata, report = self.fixture()
            metadata["bundleID"] = "org.unrelated"
            self.write_report(root, metadata=metadata, report=report)
            self.assertEqual(self.export(root, root)["status"], "absent")
            self.assertFalse((root / crash.OUTPUT_NAME).exists())

    def test_scan_does_not_recurse_or_read_old_or_oversized_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            nested = root / "nested"
            nested.mkdir()
            self.write_report(nested)
            old = self.write_report(root, "old.ips")
            os.utime(old, (self.start - 1, self.start - 1))
            oversized = root / "large.ips"
            oversized.write_bytes(b"x" * (crash.MAX_BYTES + 1))
            os.utime(oversized, (self.start + 30, self.start + 30))
            with mock.patch.object(crash, "_read_regular", wraps=crash._read_regular) as read:
                self.assertEqual(self.export(root, root)["status"], "absent")
            read.assert_not_called()

    def test_report_and_directory_scan_limits_are_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for i in range(4):
                self.write_report(root, f"report-{i}.ips")
            with mock.patch.object(crash, "MAX_REPORTS", 2):
                result = self.export(root, root)
            self.assertEqual(result["count"], 2)
            self.assertTrue(result["scan_limited"])
            (root / crash.OUTPUT_NAME).unlink()
            with mock.patch.object(crash, "MAX_ENTRIES", 0):
                result = self.export(root, root)
            self.assertEqual(result, {"status": "unavailable", "reason": "bounded_scan_limit"})

    def test_deadline_prevents_additional_file_reads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.write_report(root)
            with mock.patch.object(crash.time, "monotonic", side_effect=[0.0, 4.0]), \
                    mock.patch.object(crash, "_read_regular") as read:
                self.assertEqual(self.export(root, root)["reason"], "bounded_scan_limit")
            read.assert_not_called()

    def test_symlink_reports_are_never_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            reports = root / "reports"
            reports.mkdir()
            raw = self.write_report(root)
            try:
                (reports / "linked.ips").symlink_to(raw)
            except OSError:
                self.skipTest("File symlink creation requires privileges on this host")
            with mock.patch.object(crash, "_read_regular") as read:
                self.assertEqual(self.export(reports, root)["status"], "absent")
            read.assert_not_called()

    def test_invalid_scope_is_rejected_before_file_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for start, end in ((float("nan"), self.end), (self.end, self.start), (True, self.end)):
                with self.assertRaises(crash.package.PackageError):
                    crash.export_crash_summaries(self.udid, self.bundle, start, end, root, reports_directory=root)
            with self.assertRaises(crash.package.PackageError):
                crash.export_crash_summaries("bad-uuid", self.bundle, self.start, self.end, root, reports_directory=root)


if __name__ == "__main__":
    unittest.main()
