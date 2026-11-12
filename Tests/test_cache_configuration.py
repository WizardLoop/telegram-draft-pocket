"""Explicit disk-cache selection forwards upstream without deleting existing data."""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import package

SPEC = importlib.util.spec_from_file_location("capture_cache_configuration", ROOT / "scripts/capture-demo.py")
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


class CacheConfigurationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.checkout = self.root / "checkout"
        self.config = self.root / "config.json"
        self.cache = self.root / "existing cache"
        self.cache.mkdir()
        self.sentinel = self.cache / "existing-artifact"
        self.sentinel.write_bytes(b"preserve existing cache bytes")
        self.addCleanup(lambda: self.assertEqual(self.sentinel.read_bytes(), b"preserve existing cache bytes"))

    def test_make_command_omits_only_cache_dir_when_none(self):
        for action in ("build", "test"):
            with self.subTest(action=action):
                plain = package.make_command(self.checkout, self.config, None, action)
                cached = package.make_command(self.checkout, self.config, self.cache, action)
                self.assertNotIn("--cacheDir", plain)
                self.assertNotIn(None, plain)
                self.assertEqual(cached[:2], plain[:2])
                self.assertEqual(cached[2:4], ["--cacheDir", self.cache])
                self.assertEqual(cached[4:], plain[2:])
                self.assertEqual(plain[2:], [action, "--configurationPath", self.config,
                    "--xcodeManagedCodesigning", "--disableProvisioningProfiles"])

    def test_build_cli_forwards_no_cache_and_preserves_explicit_cache(self):
        for flags, selected in ((["--no-disk-cache"], None), (["--cache", str(self.cache)], self.cache)):
            with self.subTest(flags=flags), mock.patch.object(package, "toolchain"), \
                    mock.patch.object(package, "run") as execute:
                package.main(["build", str(self.checkout), "--configuration", str(self.config),
                              "--build-number", "123", *flags])
                self.assertEqual(execute.call_args.args[0], package.make_command(self.checkout, self.config, selected, "build") +
                                 ["--buildNumber", 123, "--configuration", "debug_sim_arm64"])
                self.assertEqual(execute.call_args.kwargs, {"cwd": self.checkout, "capture": False})

    def test_build_cli_requires_exactly_one_cache_choice(self):
        for flags in ([], ["--cache", str(self.cache), "--no-disk-cache"]):
            with self.subTest(flags=flags), mock.patch.object(package, "toolchain") as toolchain, \
                    mock.patch.object(package, "run") as execute, redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as error:
                package.main(["build", str(self.checkout), "--configuration", str(self.config),
                              "--build-number", "123", *flags])
            self.assertEqual(error.exception.code, 2)
            toolchain.assert_not_called()
            execute.assert_not_called()

    def invoke_tests(self, argv):
        with ExitStack() as stack:
            discover = stack.enter_context(mock.patch.object(package.unittest.defaultTestLoader, "discover"))
            runner = stack.enter_context(mock.patch.object(package.unittest, "TextTestRunner"))
            runner.return_value.run.return_value = mock.Mock(testsRun=1, wasSuccessful=lambda: True)
            stack.enter_context(mock.patch.object(package, "validate_package"))
            base = {"native_test_targets": ["//Tests:Feature"],
                    "native_test_simulator": {"device_type": "iPhone 17", "os_version": "27.0"}}
            toolchain = stack.enter_context(mock.patch.object(package, "toolchain", return_value=base))
            native = stack.enter_context(mock.patch.object(package, "run_native_tests"))
            stack.enter_context(redirect_stdout(io.StringIO()))
            package.main(argv)
        return discover, toolchain, native, base

    def test_native_cli_forwards_no_cache_and_preserves_explicit_cache(self):
        for flags, selected in ((["--no-disk-cache"], None), (["--cache", str(self.cache)], self.cache)):
            with self.subTest(flags=flags):
                _, _, native, base = self.invoke_tests(["test", "--native", "--checkout", str(self.checkout),
                    "--configuration", str(self.config), *flags])
                native.assert_called_once_with(self.checkout, self.config, selected, base["native_test_targets"],
                                               simulator_config=base["native_test_simulator"], artifacts=None)

    def test_native_cli_rejects_missing_or_conflicting_choice_before_running_tests(self):
        for flags in ([], ["--cache", str(self.cache), "--no-disk-cache"]):
            with self.subTest(flags=flags), mock.patch.object(package.unittest.defaultTestLoader, "discover") as discover, \
                    mock.patch.object(package, "toolchain") as toolchain, redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as error:
                package.main(["test", "--native", "--checkout", str(self.checkout),
                              "--configuration", str(self.config), *flags])
            self.assertEqual(error.exception.code, 2)
            discover.assert_not_called()
            toolchain.assert_not_called()

    def test_native_cli_still_requires_checkout_and_configuration(self):
        for flags in (["--checkout", str(self.checkout)], ["--configuration", str(self.config)]):
            with self.subTest(flags=flags), mock.patch.object(package.unittest.defaultTestLoader, "discover") as discover, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                package.main(["test", "--native", "--no-disk-cache", *flags])
            self.assertEqual(error.exception.code, 2)
            discover.assert_not_called()

    def test_native_prebuild_and_execution_both_omit_disk_cache_without_losing_destination_or_filter(self):
        session = mock.Mock(udid="CA1C775E-9033-4666-9B09-1E195CA035B5", runtime="ios-runtime")
        target, selection = "//Tests:Feature", "FeatureTests"
        with mock.patch.dict(sys.modules, {"simulator": mock.Mock(SimulatorSession=mock.Mock(return_value=session))}), \
                mock.patch.object(package, "run") as execute, redirect_stdout(io.StringIO()):
            package.run_native_tests(self.checkout, self.config, None, [{"target": target, "filter": selection}],
                simulator_config={"device_type": "iPhone 17", "os_version": "27.0"}, artifacts=self.root / "native")
        commands = [call.args[0] for call in execute.call_args_list]
        self.assertEqual(len(commands), 2)
        self.assertTrue(all("--cacheDir" not in command and None not in command for command in commands))
        self.assertEqual(commands[0][-3:], ["--target", target, "--buildOnly"])
        self.assertEqual(commands[1][-6:], ["--target", target, "--simulatorUDID", session.udid, "--testFilter", selection])
        session.cleanup.assert_called_once()

    def test_portable_test_cli_does_not_require_a_cache(self):
        discover, toolchain, native, _ = self.invoke_tests(["test"])
        discover.assert_called_once()
        toolchain.assert_not_called()
        native.assert_not_called()

    def test_capture_cli_forwards_no_cache_to_both_default_takes(self):
        with mock.patch.object(capture, "capture") as execute:
            result = capture.main(["--checkout", str(self.checkout), "--configuration", str(self.config),
                "--no-disk-cache", "--artifacts", str(self.root / "no-cache-takes")])
        self.assertEqual(result, 0)
        self.assertEqual([call.args[3].name for call in execute.call_args_list], ["story", "qa"])
        self.assertTrue(all(call.args[:3] == (self.checkout, self.config, None) for call in execute.call_args_list))

    def test_capture_cli_preserves_explicit_cache_and_optional_dark_selection(self):
        for flags, selected in ((["--no-disk-cache"], None), (["--cache", str(self.cache)], self.cache)):
            with self.subTest(flags=flags), mock.patch.object(capture, "capture") as execute:
                result = capture.main(["--checkout", str(self.checkout), "--configuration", str(self.config),
                    *flags, "--artifacts", str(self.root / ("dark-no-cache" if selected is None else "dark-cache")),
                    "--scenario", "qa-dark"])
            self.assertEqual(result, 0)
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(execute.call_args.args[:3], (self.checkout, self.config, selected))
            self.assertEqual(execute.call_args.kwargs, {"appearance": "dark"})

    def test_capture_cli_requires_exactly_one_cache_choice_before_creating_artifacts(self):
        artifacts = self.root / "not-created"
        for flags in ([], ["--cache", str(self.cache), "--no-disk-cache"]):
            with self.subTest(flags=flags), mock.patch.object(capture, "capture") as execute, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                capture.main(["--checkout", str(self.checkout), "--configuration", str(self.config),
                              "--artifacts", str(artifacts), *flags])
            self.assertEqual(error.exception.code, 2)
            execute.assert_not_called()
            self.assertFalse(artifacts.exists())


if __name__ == "__main__":
    unittest.main()
