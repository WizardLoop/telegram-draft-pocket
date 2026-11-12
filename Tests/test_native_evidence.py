"""Portable checker tests. Synthetic logs below are fixtures, never native proof."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import native_evidence as evidence


class NativeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "Integration").mkdir()
        self.base = json.loads((ROOT / "Integration/BASE.json").read_text(encoding="utf-8"))
        original = json.loads((ROOT / "Integration/files.json").read_text(encoding="utf-8"))
        paths = {row[0] for row in evidence.SOURCES}
        self.manifest = {"schema_version": 1, "base_commit": original["base_commit"], "feature": {
            "files": [entry for entry in original["feature"]["files"] if entry["source"] in paths]}}
        for path in paths:
            destination = self.root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((ROOT / path).read_bytes())
        self.write_inventory()
        self.inventory = evidence.load_inventory(self.root)
        self.expected = self.inventory["expected"]
        self.log = self.root / "native-tests.log"
        self.targets = self.root / "native-target-results.json"
        self.report = {"targets": [dict(target=target, filter=selection, build="passed", test="passed")
                                   for target, selection in evidence.TARGETS], "lifecycle_errors": []}
        self.log.write_text(self.success_log(), encoding="utf-8")
        self.write_report()

    def write_inventory(self):
        (self.root / "Integration/BASE.json").write_text(json.dumps(self.base), encoding="utf-8")
        (self.root / "Integration/files.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def write_report(self):
        self.targets.write_text(json.dumps(self.report), encoding="utf-8")

    def case_line(self, case, result="passed", *, module="ObservedNativeModule"):
        cls, method = case.split("/")
        suffix = "started." if result == "started" else result + " (0.001 seconds)."
        qualified = module + "." + cls if module else cls
        return f"Test Case '-[{qualified} {method}]' {suffix}\n"

    def success_log(self, *, omitted=None):
        lines = ["Build diagnostics are not XCTest cases.\n"]
        for target, selection in evidence.TARGETS:
            label = evidence.display_target(target, selection)
            lines.append("Starting native test target: " + label + "\n")
            for case, owner in self.expected.items():
                if owner == target and case != omitted:
                    lines.extend((self.case_line(case, "started"), self.case_line(case)))
            lines.append("Native test target PASSED: " + label + "\n")
        return "".join(lines)

    def review(self):
        return evidence.review_native_evidence(self.root, self.log, self.targets)

    def replace_log(self, before, after):
        text = self.log.read_text(encoding="utf-8")
        self.assertIn(before, text)
        self.log.write_text(text.replace(before, after, 1), encoding="utf-8")

    def mutate_source(self, data):
        path = evidence.SOURCES[0][0]
        (self.root / path).write_bytes(data)
        next(entry for entry in self.manifest["feature"]["files"] if entry["source"] == path)["sha256"] = evidence.digest(data)
        self.write_inventory()

    def assert_incomplete(self, review):
        self.assertFalse(review["complete_native_case_evidence"])

    def test_current_hash_verified_inventory_has_exact_65_cases(self):
        self.assertEqual(len(self.expected), 65)
        self.assertEqual({item["class"] for item in self.inventory["source_identity"]},
                         {item[1] for item in evidence.SOURCES})
        self.assertEqual(len(self.inventory["source_identity"]), 6)
        self.assertNotIn("DocumentTextSelectionTests", str(self.inventory))

    def test_all_real_classes_with_synthetic_complete_log_pass(self):
        review = self.review()
        self.assertTrue(review["complete_native_case_evidence"], review)
        self.assertEqual(review["passed_count"], 65)
        self.assertEqual(review["missing"], [])
        self.assertEqual(review["evidence_errors"], [])
        self.assertEqual(review["native_log_sha256"], evidence.digest(self.log.read_bytes()))
        self.assertIn("Hostless tests do not require xcresult", review["boundary"])
        observed = next(iter(review["observed_outcomes"].values()))[0]
        self.assertEqual(observed["module"], "ObservedNativeModule")

    def test_unqualified_xctest_names_are_accepted_without_guessing_module(self):
        self.log.write_text(self.log.read_text(encoding="utf-8").replace("ObservedNativeModule.", ""), encoding="utf-8")
        self.assertTrue(self.review()["complete_native_case_evidence"])

    def test_zero_cases_cannot_pass_using_successful_target_statuses(self):
        self.log.write_text("Executed 0 tests, with 0 failures.\n", encoding="utf-8")
        review = self.review()
        self.assert_incomplete(review)
        self.assertEqual(review["passed_count"], 0)
        self.assertEqual(len(review["missing"]), 65)

    def test_missing_one_case_is_incomplete_even_when_all_targets_pass(self):
        missing = next(iter(self.expected))
        self.log.write_text(self.success_log(omitted=missing), encoding="utf-8")
        review = self.review()
        self.assert_incomplete(review)
        self.assertEqual(review["missing"], [missing])
        self.assertEqual(review["passed_count"], 64)

    def test_wrong_class_is_unexpected_and_does_not_satisfy_original(self):
        case = next(iter(self.expected))
        wrong = "UnrelatedTests/" + case.split("/")[1]
        for outcome in ("started", "passed"):
            self.replace_log(self.case_line(case, outcome), self.case_line(wrong, outcome))
        review = self.review()
        self.assert_incomplete(review)
        self.assertEqual(review["unexpected_cases"], [wrong])
        self.assertIn(case, review["missing"])

    def test_failed_then_passing_retry_preserves_failure_and_duplicate(self):
        case = next(iter(self.expected))
        replacement = self.case_line(case, "failed") + self.case_line(case, "started") + self.case_line(case)
        self.replace_log(self.case_line(case), replacement)
        review = self.review()
        self.assert_incomplete(review)
        self.assertEqual(review["failed_or_skipped"], [case])
        self.assertEqual(review["repeated_outcomes"], [case])
        self.assertNotIn(case, review["passed"])

    def test_duplicate_success_or_start_and_skipped_case_fail(self):
        original = self.log.read_text(encoding="utf-8")
        case = next(iter(self.expected))
        variants = [self.case_line(case) * 2, self.case_line(case, "started") + self.case_line(case),
                    self.case_line(case, "skipped")]
        for replacement in variants:
            with self.subTest(replacement=replacement):
                self.log.write_text(original.replace(self.case_line(case), replacement, 1), encoding="utf-8")
                self.assert_incomplete(self.review())

    def test_outcome_without_start_or_truncated_case_line_fails(self):
        original = self.log.read_text(encoding="utf-8")
        case = next(iter(self.expected))
        for before, after in [(self.case_line(case, "started"), ""),
                              (self.case_line(case), self.case_line(case).rstrip()[:-1] + "\n")]:
            with self.subTest(before=before):
                self.log.write_text(original.replace(before, after, 1), encoding="utf-8")
                review = self.review()
                self.assert_incomplete(review)
                self.assertTrue(review["evidence_errors"])

    def test_case_in_wrong_target_or_repeated_target_boundary_fails(self):
        first = "Starting native test target: " + evidence.display_target(*evidence.TARGETS[0]) + "\n"
        second = "Starting native test target: " + evidence.display_target(*evidence.TARGETS[1]) + "\n"
        original = self.log.read_text(encoding="utf-8")
        for replacement in (second, first + first, ""):
            with self.subTest(replacement=replacement):
                self.log.write_text(original.replace(first, replacement, 1), encoding="utf-8")
                review = self.review()
                self.assert_incomplete(review)
                self.assertTrue(review["evidence_errors"])

    def test_target_inventory_mismatch_and_filter_narrowing_fail(self):
        original = json.loads(json.dumps(self.report))
        variants = [original["targets"][:-1], original["targets"] + [original["targets"][0]],
                    [{**original["targets"][0], "target": "//Unrelated:Tests"}] + original["targets"][1:],
                    [{**original["targets"][0], "filter": "DraftPocketTests/testOnlyOne"}] + original["targets"][1:]]
        for records in variants:
            with self.subTest(records=records):
                self.report["targets"] = records
                self.write_report()
                review = self.review()
                self.assert_incomplete(review)
                self.assertTrue(review["input_errors"])

    def test_case_after_target_termination_cannot_supply_missing_proof(self):
        case = next(iter(self.expected))
        marker = "Native test target PASSED: " + evidence.display_target(*evidence.TARGETS[0]) + "\n"
        outside = self.case_line(case, "started") + self.case_line(case)
        self.log.write_text(self.success_log(omitted=case).replace(marker, marker + outside, 1), encoding="utf-8")
        review = self.review()
        self.assert_incomplete(review)
        self.assertNotIn(case, review["passed"])
        self.assertTrue(review["evidence_errors"])

    def test_raw_target_failure_cannot_be_overridden_by_passed_json(self):
        target, selection = evidence.TARGETS[0]
        label = evidence.display_target(target, selection)
        self.replace_log("Native test target PASSED: " + label,
                         "Native test target FAILED: " + label + " [test]: runner exited 1")
        review = self.review()
        self.assert_incomplete(review)
        self.assertEqual(review["passed_count"], 65)
        self.assertEqual(review["observed_target_completions"][target][0]["result"], "failed")
        self.assertTrue(any("contradicts target results" in error for error in review["evidence_errors"]))

    def test_matching_raw_target_failure_is_not_misreported_as_a_format_error(self):
        target, selection = evidence.TARGETS[0]
        label = evidence.display_target(target, selection)
        self.replace_log("Native test target PASSED: " + label,
                         "Native test target FAILED: " + label + " [test]: runner exited 1")
        self.report["targets"][0]["test"] = "failed"
        self.write_report()
        review = self.review()
        self.assert_incomplete(review)
        self.assertEqual(review["evidence_errors"], [])
        self.assertEqual(review["observed_target_completions"][target][0]["result"], "failed")

    def test_missing_duplicate_wrong_and_malformed_completion_markers_fail(self):
        original = self.log.read_text(encoding="utf-8")
        label = evidence.display_target(*evidence.TARGETS[0])
        marker = "Native test target PASSED: " + label + "\n"
        replacements = ["", marker + marker,
                        "Native test target PASSED: " + evidence.display_target(*evidence.TARGETS[1]) + "\n",
                        "Native test target FAILED: " + label + " [unknown]: error\n"]
        for replacement in replacements:
            with self.subTest(replacement=replacement):
                self.log.write_text(original.replace(marker, replacement, 1), encoding="utf-8")
                review = self.review()
                self.assert_incomplete(review)
                self.assertTrue(review["evidence_errors"])

    def test_raw_pass_and_failed_json_are_reported_as_a_contradiction(self):
        self.report["targets"][0]["test"] = "failed"
        self.write_report()
        review = self.review()
        self.assert_incomplete(review)
        self.assertTrue(any("contradicts target results" in error for error in review["evidence_errors"]))

    def test_failed_target_or_cleanup_error_cannot_be_hidden_by_pass_lines(self):
        for failure in ("build", "test", "cleanup"):
            with self.subTest(failure=failure):
                self.report["targets"][0].update(build="passed", test="passed")
                self.report["lifecycle_errors"] = []
                if failure == "cleanup":
                    self.report["lifecycle_errors"] = ["Owned device deletion failed"]
                else:
                    self.report["targets"][0][failure] = "failed"
                self.write_report()
                self.assert_incomplete(self.review())

    def test_changed_base_registration_is_rejected(self):
        self.base["native_test_targets"][0] = {"target": evidence.TARGETS[0][0], "filter": "DraftPocketTests"}
        self.write_inventory()
        self.assertTrue(self.review()["input_errors"])

    def test_hash_mismatch_redirected_source_and_duplicate_manifest_are_rejected(self):
        original = json.loads(json.dumps(self.manifest))
        for mutation in ("hash", "path", "duplicate"):
            with self.subTest(mutation=mutation):
                self.manifest = json.loads(json.dumps(original))
                entry = self.manifest["feature"]["files"][0]
                if mutation == "hash":
                    entry["sha256"] = "0" * 64
                elif mutation == "path":
                    entry["source"] = "../outside.swift"
                else:
                    self.manifest["feature"]["files"].append(dict(entry))
                self.write_inventory()
                self.assertTrue(self.review()["input_errors"])

    def test_hash_valid_malformed_source_and_wrong_utf8_fail_closed(self):
        variants = [
            b"\xff", b"final class OtherTests: XCTestCase {\n    func testOne() {}\n}\n",
            b"final class DraftPocketTests: XCTestCase {\n    func testOne(value: Int) {\n    }\n}\n",
            b"final class DraftPocketTests: XCTestCase {\n    func testOne() {\n}\n",
            b"final class DraftPocketTests: XCTestCase {\n#if DEBUG\n    func testOne() {\n    }\n#endif\n}\n",
            b"final class DraftPocketTests: XCTestCase {\n    func testOne() {\n    }\n    func testOne() {\n    }\n}\n",
            b"final class DraftPocketTests: XCTestCase {\n}\nfunc helper() {\n    func testOutside() {\n    }\n}\n",
        ]
        for source in variants:
            with self.subTest(source=source):
                self.mutate_source(source)
                review = self.review()
                self.assert_incomplete(review)
                self.assertTrue(review["input_errors"])

    def test_comment_and_string_pseudotests_do_not_enter_inventory(self):
        source = '''// final class FakeTests: XCTestCase {
final class DraftPocketTests: XCTestCase {
    /* nested comment /* } */ func testFake() { */
    func testReal() throws {
        let text = "func testFake() { }"
        let escaped = "a \\"quote\\" }"
    }
}
'''
        self.assertEqual(evidence.authored_cases(source.encode(), "DraftPocketTests", "fixture.swift"),
                         ["DraftPocketTests/testReal"])

    def test_invalid_log_utf8_and_duplicate_json_keys_fail(self):
        self.log.write_bytes(b"\xff")
        self.assertTrue(self.review()["input_errors"])
        self.log.write_text(self.success_log(), encoding="utf-8")
        self.targets.write_text('{"targets": [], "targets": [], "lifecycle_errors": []}', encoding="utf-8")
        self.assertTrue(self.review()["input_errors"])

    def test_cli_saves_incomplete_review_and_returns_failure(self):
        self.log.write_text("No native cases ran.\n", encoding="utf-8")
        output = self.root / "review.json"
        with redirect_stdout(io.StringIO()):
            code = evidence.main(["--package", str(self.root), "--log", str(self.log),
                                  "--targets", str(self.targets), "--output", str(output)])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output.read_text(encoding="utf-8"))["complete_native_case_evidence"])

    def test_cli_cannot_overwrite_log_or_source(self):
        original = self.log.read_bytes()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            evidence.main(["--package", str(self.root), "--log", str(self.log),
                           "--targets", str(self.targets), "--output", str(self.log)])
        self.assertEqual(self.log.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
