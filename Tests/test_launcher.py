"""Existing-device reuse must prove the launcher's dedicated-device identity."""

import ast
import importlib.util
from pathlib import Path
import re
import textwrap
from types import SimpleNamespace
import unittest
import uuid

SPEC = importlib.util.spec_from_file_location("demo_launcher", Path(__file__).parents[1] / "Demo/run-demo.py")
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class LauncherIdentityTests(unittest.TestCase):
    def setUp(self):
        self.identity = {"udid": "DEVICE", "name": "Draft Pocket isolated fixture", "runtime": "IOS27", "bundle_id": "org.example.demo"}
        self.devices = {"IOS27": [{"udid": "DEVICE", "name": "Draft Pocket isolated fixture", "isAvailable": True, "state": "Shutdown"}]}

    def check(self):
        return launcher.owned_device(self.identity, self.devices, "DEVICE", "IOS27", "org.example.demo")

    def test_matching_recorded_device_accepted(self):
        self.assertEqual(self.check()["state"], "Shutdown")

    def test_other_runtime_name_account_or_uuid_rejected(self):
        for key in ("runtime", "name", "bundle_id", "udid"):
            original = self.identity[key]
            self.identity[key] = "unrelated"
            with self.subTest(key=key), self.assertRaises(launcher.package.PackageError):
                self.check()
            self.identity[key] = original

    def test_renamed_missing_or_unavailable_device_rejected(self):
        for devices in ({}, {"IOS27": []}, {"IOS27": [{"udid": "DEVICE", "name": "User phone", "isAvailable": True}]},
                        {"IOS27": [{"udid": "DEVICE", "name": "Draft Pocket isolated fixture", "isAvailable": False}]}):
            self.devices = devices
            with self.subTest(devices=devices), self.assertRaises(launcher.package.PackageError):
                self.check()


class MakeSimulatorDestinationTests(unittest.TestCase):
    """Execute the packaged Make destination code, without importing its macOS tools."""

    @classmethod
    def setUpClass(cls):
        patch = (Path(__file__).parents[1] / "Integration/telegram-ios.patch").read_text(encoding="utf-8")
        marker = "diff --git a/build-system/Make/Make.py b/build-system/Make/Make.py\n"
        section = patch.split(marker, 1)[1].split("\ndiff --git ", 1)[0]
        hunks = ["\n".join(line[1:] for line in hunk.splitlines() if line.startswith((" ", "+")))
                 for hunk in re.split(r"^@@[^\n]*\n", section, flags=re.MULTILINE)[1:]]
        method_hunks = [hunk for hunk in hunks if "    def invoke_test(" in hunk]
        if len(method_hunks) != 1:
            raise AssertionError("The packaged Make test invocation must have one executable hunk")
        method = "    def invoke_test(" + method_hunks[0].split("    def invoke_test(", 1)[1]
        # This patch hunk ends after constructing the relevant arguments. Return them
        # from the unchanged code instead of invoking a real Bazel/macOS process.
        scope = {"uuid": uuid}
        exec(compile(textwrap.dedent(method) + "\n    return combined_arguments\n", "packaged Make.invoke_test", "exec"), scope)
        cls.invoke = staticmethod(scope["invoke_test"])
        parser_hunks = [hunk for hunk in hunks if "'--simulatorUDID'" in hunk]
        if len(parser_hunks) != 1:
            raise AssertionError("The packaged Make UUID argument must have one definition")
        parser_source = parser_hunks[0]
        flag = parser_source.index("'--simulatorUDID'")
        start = parser_source.rfind("    testParser.add_argument(", 0, flag)
        end = parser_source.index("\n    )", flag) + len("\n    )")
        call = ast.parse(textwrap.dedent(parser_source[start:end])).body[0].value
        converter = next(keyword.value for keyword in call.keywords if keyword.arg == "type")
        cls.convert = staticmethod(eval(compile(ast.Expression(converter), "packaged Make UUID parser", "eval"), {"uuid": uuid}))

    def command(self, target, value, **kwargs):
        owner = SimpleNamespace(build_environment=SimpleNamespace(bazel_path="pinned-bazel"),
                                get_startup_bazel_arguments=lambda: [], disable_provisioning_profiles=True,
                                common_args=[], common_build_args=[], get_define_arguments=lambda: [])
        return self.invoke(owner, test_target=target, simulator_udid=value, test_filter="SelectedTests", **kwargs)

    def test_hosted_and_ui_test_destinations_keep_simctl_uuid_case(self):
        canonical = "5509B07B-316A-4069-94ED-A83593AEA959"
        targets = ("//Tests/ComposerHarness:DraftPocketSelectionHarnessTests",
                   "//Tests/DraftPocketDemo:DraftPocketDemoUITests")
        for target in targets:
            for value in (canonical, canonical.lower()):
                with self.subTest(target=target, value=value):
                    self.assertEqual(self.convert(value), canonical)
                    arguments = self.command(target, value)
                    self.assertEqual([arg for arg in arguments if arg.startswith("--test_arg=--destination=")],
                                     ["--test_arg=--destination=platform=ios_simulator,id=" + canonical])
                    self.assertIn(target, arguments)
                    self.assertIn("--test_filter=SelectedTests", arguments)

    def test_malformed_uuid_rejected_before_bazel_arguments_are_accepted(self):
        for value in ("not-a-device", "5509B07B-316A-4069-94ED-A83593AEA959 --destination=other"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.convert(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.command("//Tests/ComposerHarness:DraftPocketSelectionHarnessTests", value)


if __name__ == "__main__":
    unittest.main()
