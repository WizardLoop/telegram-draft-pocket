"""Validate secret configuration without executing or exposing its values."""

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("package", ROOT / "scripts/package.py")
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


class ConfigurationTests(unittest.TestCase):
    def config(self):
        value = json.loads((ROOT / "Integration/build-configuration.example.json").read_text(encoding="utf-8"))
        # Deliberately synthetic syntax-test values; never used for a build or API request.
        value["api_id"] = "123456"
        value["api_hash"] = "a" * 32
        return value

    def test_simulator_marker_needs_no_apple_credential(self):
        package.validate_configuration(self.config())

    def test_every_required_field_is_checked(self):
        for key in self.config():
            value = self.config()
            del value[key]
            with self.subTest(field=key), self.assertRaises(package.PackageError):
                package.validate_configuration(value)

    def test_template_placeholders_are_rejected(self):
        value = json.loads((ROOT / "Integration/build-configuration.example.json").read_text(encoding="utf-8"))
        with self.assertRaises(package.PackageError):
            package.validate_configuration(value)

    def test_invalid_secret_is_not_echoed(self):
        value = self.config()
        secret = 'private-value-which-must-not-appear\"\nexecutable()'
        value["api_hash"] = secret
        with self.assertRaises(package.PackageError) as caught:
            package.validate_configuration(value)
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn("executable", str(caught.exception))

    def test_starlark_interpolation_is_not_executable_input(self):
        for key in ("bundle_id", "team_id", "app_center_id", "app_specific_url_scheme", "premium_iap_product_id"):
            value = self.config()
            value[key] = 'x\"\nfail(\"unexpected\")\n'
            with self.subTest(field=key), self.assertRaises(package.PackageError):
                package.validate_configuration(value)

    def test_api_id_bounds_and_type(self):
        for api_id in (True, 0, -1, 2147483648, [], "not-issued"):
            value = self.config()
            value["api_id"] = api_id
            with self.subTest(api_id=api_id), self.assertRaises(package.PackageError):
                package.validate_configuration(value)

    def test_marker_cannot_claim_cloud_entitlements(self):
        for key in ("enable_siri", "enable_icloud"):
            value = self.config()
            value[key] = True
            with self.subTest(field=key), self.assertRaises(package.PackageError):
                package.validate_configuration(value)

    def test_wrong_container_and_extra_fields_fail(self):
        for value in ([], None, {**self.config(), "unexpected": "private"}):
            with self.assertRaises(package.PackageError):
                package.validate_configuration(value)


if __name__ == "__main__":
    unittest.main()
