"""Reject plausible-looking recordings that lack real scenario evidence."""

import importlib.util
import copy
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
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


class BinaryProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.udid = "CA1C775E-9033-4666-9B09-1E195CA035B5"
        self.application = "A3159F14-508D-4CB9-946D-84ED2F4FEFAE"
        self.bundle_id = "org.example.draftpocket"
        self.target = "//Tests/DraftPocketDemo:DraftPocketDemoUITests"

    def create_runner(self, checkout, artifact, host="Telegram/Telegram.ipa"):
        runner = checkout / "bazel-bin/Tests/DraftPocketDemo/DraftPocketDemoUITests"
        runner.parent.mkdir(parents=True, exist_ok=True)
        runner.write_text('#!/bin/bash\nTEST_HOST_PATH="' + host + '"\n', encoding="utf-8")
        manifest = runner.with_name(runner.name + ".runfiles_manifest")
        manifest.write_text("_main/" + host + " " + str(artifact) + "\n", encoding="utf-8")
        return runner, manifest

    def create_ipa(self, app, ipa):
        ipa.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(ipa, "w") as archive:
            for file in app.rglob("*"):
                if file.is_file():
                    archive.write(file, "Payload/Telegram.app/" + file.relative_to(app).as_posix())

    def create_alias(self, alias, physical):
        if os.name == "nt":
            # Directory junctions need no symbolic-link privilege on Windows.
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                "New-Item -ItemType Junction -Path $env:DRAFT_TEST_ALIAS -Target $env:DRAFT_TEST_PHYSICAL | Out-Null"],
                env={**os.environ, "DRAFT_TEST_ALIAS": str(alias), "DRAFT_TEST_PHYSICAL": str(physical)},
                check=True, capture_output=True)
        else:
            alias.symlink_to(physical, target_is_directory=True)

    def create_app(self, app):
        for prefix, identifier, executable in (("", self.bundle_id, "Telegram"),
                ("Frameworks/TelegramUIFramework.framework", self.bundle_id + ".TelegramUI", "TelegramUIFramework"),
                ("Frameworks/TelegramCoreFramework.framework", self.bundle_id + ".TelegramCore", "TelegramCoreFramework")):
            destination = app / prefix
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": identifier,
                "CFBundleExecutable": executable, "CFBundleSupportedPlatforms": ["iPhoneSimulator"]}))
            (destination / executable).write_bytes(("compiled test fixture " + executable).encode())

    def test_fresh_ipa_and_installed_main_ui_core_files_match_exactly(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "Devices" / self.udid / "data/Containers/Bundle/Application" / self.application / "Telegram.app"
            self.create_app(app)
            checkout = root / "checkout"
            ipa = checkout / "bazel-out/ios_sim_arm64-dbg-transition/bin/Telegram/Telegram.ipa"
            self.create_ipa(app, ipa)
            self.create_runner(checkout, ipa)
            # A stale app-target output must never win over the UI test's host.
            stale = checkout / "bazel-bin/Telegram/Telegram.ipa"
            stale.parent.mkdir()
            stale.write_bytes(b"stale prior app build")
            built = capture.built_binary_inventory(checkout, self.bundle_id, self.target)
            result = capture.verify_installed_binaries(app, root / "Devices", self.udid, self.bundle_id, built)
            self.assertEqual(result["status"], "matched")
            self.assertEqual(len(result["files"]), 6)
            self.assertEqual(result["udid"], self.udid)
            self.assertEqual(result["files"][1]["sha256"], hashlib.sha256((app / "Telegram").read_bytes()).hexdigest())
            self.assertNotIn(str(root), json.dumps(result))
            self.assertEqual(built["source"], "bazel-out/ios_sim_arm64-dbg-transition/bin/Telegram/Telegram.ipa")
            self.assertEqual(built["declared_test_host"], "Telegram/Telegram.ipa")
            self.assertEqual(built["test_target"], self.target)

    def test_device_root_alias_matches_but_inner_device_alias_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            physical = root / "physical"
            physical.mkdir()
            alias = root / "alias"
            self.create_alias(alias, physical)
            devices = physical / "Devices"
            app = devices / self.udid / "data/Containers/Bundle/Application" / self.application / "Telegram.app"
            self.create_app(app)
            aliased_app = alias / app.relative_to(physical)
            # Model /var and /private/var spellings in both directions.
            self.assertEqual(capture.owned_app_container(aliased_app, devices, self.udid), app)
            self.assertEqual(capture.owned_app_container(app, alias / "Devices", self.udid), app)
            other_container = app.parent.with_name("B3159F14-508D-4CB9-946D-84ED2F4FEFAE")
            self.create_alias(other_container, app.parent)
            with self.assertRaises(capture.package.PackageError):
                capture.owned_app_container(other_container / "Telegram.app", devices, self.udid)

    def test_manifest_cannot_select_an_outside_or_ambiguous_test_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            checkout = root / "checkout"
            ipa = checkout / "bazel-out/ios-transition/bin/Telegram/Telegram.ipa"
            ipa.parent.mkdir(parents=True)
            ipa.write_bytes(b"host")
            runner, manifest = self.create_runner(checkout, ipa)
            valid = manifest.read_text(encoding="utf-8")
            outside = root / "outside/Telegram.ipa"
            outside.parent.mkdir()
            outside.write_bytes(b"not this Bazel output")
            for mapping in (valid + valid, "_main/Telegram/Telegram.ipa " + str(outside) + "\n",
                            "_main/Telegram/Telegram.ipa relative/Telegram.ipa\n", "_main/other.ipa " + str(ipa) + "\n"):
                manifest.write_text(mapping, encoding="utf-8")
                with self.subTest(mapping=mapping), self.assertRaises(capture.package.PackageError):
                    capture.declared_test_host(checkout, self.target)
            manifest.write_text(valid, encoding="utf-8")
            for host in ("../Telegram/Telegram.ipa", "$(pwd)/Telegram.ipa", "/Telegram/Telegram.ipa"):
                runner.write_text('TEST_HOST_PATH="' + host + '"\n', encoding="utf-8")
                with self.subTest(host=host), self.assertRaises(capture.package.PackageError):
                    capture.declared_test_host(checkout, self.target)
            runner.write_text('TEST_HOST_PATH="Telegram/Telegram.ipa"\n' * 2, encoding="utf-8")
            with self.assertRaises(capture.package.PackageError):
                capture.declared_test_host(checkout, self.target)

    def test_manifest_paths_with_spaces_and_escaped_records_select_one_exact_app(self):
        with tempfile.TemporaryDirectory(prefix="draft pocket ") as temporary:
            checkout = Path(temporary).resolve() / "checkout"
            app = checkout / "bazel-out/ios-transition/bin/Telegram/Telegram.app"
            self.create_app(app)
            _, manifest = self.create_runner(checkout, app, "Telegram/Telegram.app")
            expected = capture.directory_binary_inventory(app, self.bundle_id)
            escaped_path = str(app).replace("\\", "\\b")
            # Bazel escapes spaces in logical keys, but leaves target spaces
            # literal: consumers split the record only at the first separator.
            auxiliary = " _main/auxiliary\\sdata " + escaped_path + "\n"
            for mapping in (manifest.read_text(encoding="utf-8") + auxiliary,
                            " _main/Telegram/Telegram.app " + escaped_path + "\n",
                            " _main/Telegram/Telegram.app " + escaped_path.replace(" ", "\\s") + "\n"):
                manifest.write_text(mapping, encoding="utf-8")
                built = capture.built_binary_inventory(checkout, self.bundle_id, self.target)
                self.assertEqual(built["files"], expected["files"])

    def test_external_bazel_output_links_use_the_transitioned_declared_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            checkout = root / "checkout"
            checkout.mkdir()
            outputs = root / "external-cache/execroot/_main/bazel-out"
            host_bin = outputs / "darwin_arm64-dbg/bin"
            host_bin.mkdir(parents=True)
            self.create_alias(checkout / "bazel-bin", host_bin)
            self.create_alias(checkout / "bazel-out", outputs)
            app = outputs / "ios_sim_arm64-dbg-transition/bin/Telegram/Telegram.app"
            self.create_app(app)
            self.create_runner(checkout, app, "Telegram/Telegram.app")
            built = capture.built_binary_inventory(checkout, self.bundle_id, self.target)
            self.assertEqual(built["source"], "bazel-out/ios_sim_arm64-dbg-transition/bin/Telegram/Telegram.app")
            self.assertEqual(len(built["files"]), 6)
            self.assertFalse((host_bin / "Telegram").exists())

    def test_manifest_artifact_links_below_output_root_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary).resolve() / "checkout"
            app = checkout / "bazel-out/physical/bin/Telegram/Telegram.app"
            self.create_app(app)
            alias = checkout / "bazel-out/alias"
            self.create_alias(alias, checkout / "bazel-out/physical")
            self.create_runner(checkout, alias / "bin/Telegram/Telegram.app", "Telegram/Telegram.app")
            with self.assertRaises(capture.package.PackageError):
                capture.declared_test_host(checkout, self.target)

    def test_changed_executable_or_metadata_is_not_provenance(self):
        for name in ("Telegram", "Info.plist", "Frameworks/TelegramCoreFramework.framework/TelegramCoreFramework"):
            with self.subTest(path=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                app = root / self.udid / "data/Containers/Bundle/Application" / self.application / "Telegram.app"
                self.create_app(app)
                built = {"source": "test fresh build", **capture.directory_binary_inventory(app, self.bundle_id)}
                target = app / name
                target.write_bytes(target.read_bytes() + b" ")
                with self.assertRaises((capture.package.PackageError, plistlib.InvalidFileException)):
                    capture.verify_installed_binaries(app, root, self.udid, self.bundle_id, built)

    def test_other_device_relative_path_and_nested_app_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for candidate in (Path("relative/Telegram.app"), root / "another-device/Telegram.app",
                              root / self.udid / "data/Containers/Bundle/Application" / self.application / "nested/Telegram.app"):
                with self.subTest(path=candidate), self.assertRaises(capture.package.PackageError):
                    capture.owned_app_container(candidate, root, self.udid)

    def test_bundle_executable_traversal_and_wrong_identity_are_rejected_before_hash(self):
        for executable in ("../outside", "/absolute", "nested/executable", "C:\\outside"):
            with self.subTest(executable=executable):
                metadata = {"CFBundleIdentifier": self.bundle_id, "CFBundleExecutable": executable,
                            "CFBundleSupportedPlatforms": ["iPhoneSimulator"]}
                hash_file = mock.Mock()
                with self.assertRaises(capture.package.PackageError):
                    capture.app_binary_inventory(lambda _: metadata, hash_file, self.bundle_id)
                hash_file.assert_not_called()
        with self.assertRaises(capture.package.PackageError):
            capture.app_binary_inventory(lambda _: {"CFBundleIdentifier": "another.app"}, mock.Mock(), self.bundle_id)

    def test_missing_build_artifact_and_archive_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            outputs = root / "bazel-out/ios-transition/bin/Telegram"
            outputs.mkdir(parents=True)
            self.create_runner(root, outputs / "Telegram.ipa")
            with self.assertRaises(capture.package.PackageError):
                capture.built_binary_inventory(root, self.bundle_id, self.target)
            with zipfile.ZipFile(outputs / "Telegram.ipa", "w") as archive:
                entry = zipfile.ZipInfo("Payload/Telegram.app/Info.plist")
                entry.external_attr = (0o120777 << 16)
                archive.writestr(entry, b"outside")
            with self.assertRaises(capture.package.PackageError):
                capture.built_binary_inventory(root, self.bundle_id, self.target)


if __name__ == "__main__":
    unittest.main()
