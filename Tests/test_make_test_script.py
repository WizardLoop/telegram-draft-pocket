"""Exercise actual packaged Make code without importing macOS build tools."""

import argparse
import ast
from contextlib import redirect_stderr
import io
import os
from pathlib import Path
import re
import stat
import textwrap
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid


ROOT = Path(__file__).resolve().parents[1]


def make_blocks():
    # Explicit authoring override only. CI reads the exported patch, never a
    # conveniently present checkout or a separately maintained implementation.
    override = os.environ.get("DRAFT_POCKET_MAKE_TEST_SOURCE")
    if override:
        return [Path(override).read_text(encoding="utf-8")]
    patch = (ROOT / "Integration/telegram-ios.patch").read_text(encoding="utf-8")
    marker = "diff --git a/build-system/Make/Make.py b/build-system/Make/Make.py\n"
    section = patch.split(marker, 1)[1].split("\ndiff --git ", 1)[0]
    return ["\n".join(line[1:] for line in hunk.splitlines() if line.startswith((" ", "+")))
            for hunk in re.split(r"^@@[^\n]*\n", section, flags=re.MULTILINE)[1:]]


def function_source(blocks, name):
    matches = []
    pattern = re.compile(r"^( *)def " + re.escape(name) + r"\(", re.MULTILINE)
    for block in blocks:
        for match in pattern.finditer(block):
            lines = block[match.start():].splitlines()
            indentation = len(match.group(1))
            stop = len(lines)
            for index, line in enumerate(lines[1:], 1):
                if line.strip() and len(line) - len(line.lstrip()) <= indentation:
                    stop = index
                    break
            matches.append(textwrap.dedent("\n".join(lines[:stop])))
    if len(matches) != 1:
        raise AssertionError(f"Expected one actual Make.{name} block, found {len(matches)}")
    return matches[0]


def exact_statement(blocks, pattern):
    matches = [match.group(0) for block in blocks for match in re.finditer(pattern, block, re.MULTILINE)]
    if len(matches) != 1:
        raise AssertionError(f"Expected one actual Make statement, found {len(matches)}")
    source = textwrap.dedent(matches[0])
    return compile(ast.parse(source), "actual Make statement", "exec")


class MakeTestScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        blocks = make_blocks()
        scope = {"os": os, "stat": stat, "uuid": uuid}
        for name in ("validate_test_script_path", "protect_test_script"):
            exec(compile(ast.parse(function_source(blocks, name)), f"actual Make.{name}", "exec"), scope)
        cls.validate = staticmethod(scope["validate_test_script_path"])
        cls.protect = staticmethod(scope["protect_test_script"])

        # The exported hunk contains the complete changed argument construction,
        # through the unchanged defines. Do not execute its external Bazel call.
        invocation = function_source(blocks, "invoke_test")
        endpoint = "    combined_arguments += self.get_define_arguments()"
        invocation = invocation.split(endpoint, 1)[0] + endpoint + "\n    return combined_arguments\n"
        exec(compile(ast.parse(invocation), "actual Make.invoke_test arguments", "exec"), scope)
        cls.invoke = staticmethod(scope["invoke_test"])
        cls.finish = exact_statement(blocks,
            r"^        call_executable\(combined_arguments\)\n"
            r"        if script_path is not None:\n            protect_test_script\(script_path\)")
        cls.forward = exact_statement(blocks,
            r"^    bazel_command_line\.invoke_test\(test_target=arguments\.target,[\s\S]*?"
            r"script_path=arguments\.scriptPath\)")
        cls.uuid_argument = exact_statement(blocks,
            r"^    testParser\.add_argument\(\n        '--simulatorUDID',[\s\S]*?\n    \)")
        cls.mode_arguments = exact_statement(blocks,
            r"^    testModeGroup = testParser\.add_mutually_exclusive_group\(\)\n"
            r"[\s\S]*?testModeGroup\.add_argument\('--scriptPath',[\s\S]*?\)\n")
        cls.required_uuid = exact_statement(blocks,
            r"^    if args\.commandName == 'test' and args\.scriptPath is not None and args\.simulatorUDID is None:\n"
            r"        parser\.error\('--scriptPath requires --simulatorUDID'\)")

    def setUp(self):
        import tempfile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.script = self.root / "test runner.sh"
        self.udid = "5509B07B-316A-4069-94ED-A83593AEA959"
        self.owner = SimpleNamespace(
            build_environment=SimpleNamespace(bazel_path="pinned-bazel"),
            get_startup_bazel_arguments=lambda: ["--output_user_root=/private/build root"],
            disable_provisioning_profiles=True,
            common_args=["--announce_rc"], common_build_args=["--build-setting=kept"],
            get_define_arguments=lambda: ["--define=configuration-kept"])

    def command(self, **kwargs):
        return self.invoke(self.owner, test_target="//Tests/DraftPocketDemo:DraftPocketDemoUITests",
                           **kwargs)

    def parser(self, arguments):
        parser = argparse.ArgumentParser()
        parser.set_defaults(commandName="test")
        scope = {"testParser": parser, "uuid": uuid, "validate_test_script_path": self.validate}
        exec(self.uuid_argument, scope)
        exec(self.mode_arguments, scope)
        parsed = parser.parse_args(arguments)
        exec(self.required_uuid, {"args": parsed, "parser": parser})
        return parsed

    def test_emits_public_run_command_with_exact_target_filter_destination_and_build_flags(self):
        arguments = self.command(simulator_udid=self.udid.lower(), test_filter="DemoTests/testStory",
                                 script_path=str(self.script))
        self.assertEqual(arguments[:3], ["pinned-bazel", "--output_user_root=/private/build root", "run"])
        self.assertIn("--script_path=" + str(self.script), arguments)
        self.assertIn("--test_arg=--destination=platform=ios_simulator,id=" + self.udid, arguments)
        self.assertIn("--test_filter=DemoTests/testStory", arguments)
        self.assertIn("//Tests/DraftPocketDemo:DraftPocketDemoUITests", arguments)
        self.assertEqual(arguments[-4:], ["--//Telegram:disableProvisioningProfiles", "--announce_rc",
                                         "--build-setting=kept", "--define=configuration-kept"])
        self.assertFalse(any(arg.startswith(("--test_output=", "--cache_test_results=")) for arg in arguments))
        self.assertFalse(self.script.exists())

    def test_default_build_and_test_modes_remain_unchanged(self):
        test = self.command(simulator_udid=self.udid, test_filter="SomeTests")
        self.assertEqual(test[2], "test")
        self.assertIn("--cache_test_results=no", test)
        self.assertIn("--test_output=all", test)
        self.assertIn("--test_output=errors", self.command())
        build = self.command(simulator_udid=self.udid, test_filter="SomeTests", build_only=True)
        self.assertEqual(build[2], "build")
        self.assertFalse(any(arg.startswith(("--test_", "--cache_test_results=", "--script_path=")) for arg in build))

    def test_programmatic_emission_requires_uuid_and_rejects_build_only(self):
        for kwargs in ({}, {"simulator_udid": self.udid, "build_only": True}, {"simulator_udid": "bad-uuid"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.command(script_path=str(self.script), **kwargs)
        self.assertFalse(self.script.exists())

    def test_cli_excludes_build_only_and_requires_valid_uuid(self):
        cases = (["--scriptPath", str(self.script)],
                 ["--scriptPath", str(self.script), "--buildOnly", "--simulatorUDID", self.udid],
                 ["--scriptPath", str(self.script), "--simulatorUDID", "bad-uuid"])
        for argv in cases:
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                self.parser(argv)
            self.assertEqual(error.exception.code, 2)
        parsed = self.parser(["--scriptPath", str(self.script), "--simulatorUDID", self.udid.lower()])
        self.assertEqual(parsed.scriptPath, str(self.script))
        self.assertEqual(parsed.simulatorUDID, self.udid)
        self.assertFalse(parsed.buildOnly)

    def test_paths_require_existing_parent_and_never_overwrite(self):
        existing = self.root / "preserved.sh"
        existing.write_bytes(b"existing runner bytes")
        for path in ("relative.sh", self.root / "missing" / "runner.sh", existing, self.root):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.validate(path)
        self.assertEqual(existing.read_bytes(), b"existing runner bytes")

    @unittest.skipUnless(os.name == "posix", "POSIX ownership and private-directory permissions")
    def test_parent_must_be_private_and_owned(self):
        self.root.chmod(0o755)
        try:
            with self.assertRaises(ValueError):
                self.validate(self.script)
        finally:
            self.root.chmod(0o700)
        real_stat = os.stat
        def other_owner(path, *args, **kwargs):
            info = real_stat(path, *args, **kwargs)
            if os.fspath(path) == str(self.root):
                return SimpleNamespace(st_mode=info.st_mode, st_uid=os.geteuid() + 1)
            return info
        with mock.patch.object(os, "stat", side_effect=other_owner), self.assertRaises(ValueError):
            self.validate(self.script)

    def make_symlink(self, link, destination, directory=False):
        try:
            link.symlink_to(destination, target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Symlinks unavailable: {error}")

    def test_symlink_destination_or_parent_is_rejected(self):
        self.make_symlink(self.script, self.root / "absent")
        with self.assertRaises(ValueError):
            self.validate(self.script)
        parent_link = self.root / "parent-link"
        self.make_symlink(parent_link, self.root, directory=True)
        with self.assertRaises(ValueError):
            self.validate(parent_link / "other.sh")

    def test_script_path_is_one_argument_even_with_shell_characters(self):
        path = self.root / "runner $literal & space.sh"
        arguments = self.command(simulator_udid=self.udid, script_path=str(path))
        self.assertEqual([arg for arg in arguments if arg.startswith("--script_path=")], ["--script_path=" + str(path)])

    def test_generated_script_is_protected_without_execution_or_content_change(self):
        raw = b"#!/bin/bash\nexit 99\n"
        calls = []
        def generate(arguments):
            calls.append(arguments)
            self.script.write_bytes(raw)
        exec(self.finish, {"call_executable": generate, "combined_arguments": ["pinned-bazel", "run"],
                           "script_path": str(self.script), "protect_test_script": self.protect})
        self.assertEqual(calls, [["pinned-bazel", "run"]])
        self.assertEqual(self.script.read_bytes(), raw)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.script.stat().st_mode), 0o700)

    def test_generation_failure_propagates_without_running_or_protecting_script(self):
        failure = RuntimeError("Bazel generation failed")
        protect = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, "generation failed"):
            exec(self.finish, {"call_executable": mock.Mock(side_effect=failure), "combined_arguments": [],
                               "script_path": str(self.script), "protect_test_script": protect})
        protect.assert_not_called()
        self.assertFalse(self.script.exists())

    def test_generated_path_must_be_a_regular_file(self):
        self.script.mkdir()
        with self.assertRaises(ValueError):
            self.protect(self.script)
        self.script.rmdir()
        original = self.root / "original.sh"
        original.write_bytes(b"preserve")
        self.make_symlink(self.script, original)
        with self.assertRaises(ValueError):
            self.protect(self.script)
        self.assertEqual(original.read_bytes(), b"preserve")

    def test_main_test_function_forwards_selected_mode_without_changing_fields(self):
        owner = mock.Mock()
        args = SimpleNamespace(target="//Tests:Exact", simulatorUDID=self.udid, buildOnly=False,
                               testFilter="Exact/testMethod", scriptPath=str(self.script))
        exec(self.forward, {"bazel_command_line": owner, "arguments": args})
        owner.invoke_test.assert_called_once_with(test_target=args.target, simulator_udid=self.udid,
            build_only=False, test_filter=args.testFilter, script_path=str(self.script))


if __name__ == "__main__":
    unittest.main()
