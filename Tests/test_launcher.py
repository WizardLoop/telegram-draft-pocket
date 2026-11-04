"""Existing-device reuse must prove the launcher's dedicated-device identity."""

import importlib.util
from pathlib import Path
import unittest

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


if __name__ == "__main__":
    unittest.main()
