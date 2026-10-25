"""Tests proving the Autonomous QA & Self-Healing Development Loop."""

import sys
import tempfile
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from browser_agent.autonomous_qa.auditor import QAAuditor
from browser_agent.autonomous_qa.fixer import Fixer
from browser_agent.autonomous_qa.models import (
    DefectReport,
    FixRecord,
    Severity,
    ValidationResult,
    Verdict,
)
from browser_agent.autonomous_qa.orchestrator import AutonomousQAOrchestrator
from browser_agent.autonomous_qa.validator import IndependentValidator


class AutonomousQALoopTests(unittest.TestCase):
    """Proves the full closed-loop Autonomous QA architecture: Audit -> Fix -> Validate -> Orchestrate."""

    def test_auditor_identifies_and_structures_defect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            auditor = QAAuditor(root)

            # Simulated unittest failure output
            mock_output = (
                "FAIL: test_quiz_calculation (test_quiz.QuizTests)\n"
                "AssertionError: 2 != 3\n"
                "Ran 5 tests in 0.010s\n"
                "FAILED (failures=1)"
            )
            report = auditor._parse_failure(mock_output)
            self.assertIsNotNone(report)
            self.assertEqual(report.defect_id, "DEFECT-test_quiz_calculation")
            self.assertEqual(report.severity, Severity.HIGH)
            self.assertEqual(report.component, "test_quiz.QuizTests")
            self.assertIn("Assertion in test_quiz_calculation", report.expected_behavior)
            self.assertGreaterEqual(report.confidence, 0.90)

    def test_fixer_applies_patch_and_records_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target_file = root / "calculator.py"
            target_file.write_text("def add(a, b): return a - b\n", encoding="utf-8")

            fixer = Fixer(root, branch_name="test-fix-branch")
            defect = DefectReport(
                defect_id="DEFECT-001",
                title="Calculator subtraction bug",
                severity=Severity.HIGH,
                component="calculator",
                file_path=str(target_file),
                line_number=1,
                reproduction_steps=["add(2, 2) returns 0 instead of 4"],
                expected_behavior="add(2, 2) == 4",
                actual_behavior="add(2, 2) == 0",
                confidence=1.0,
            )

            def patch_fn(workspace: Path) -> list[str]:
                f = workspace / "calculator.py"
                f.write_text("def add(a, b): return a + b\n", encoding="utf-8")
                return [str(f)]

            fix_record = fixer.apply_fix(defect, patch_fn, "test_add_correct_sum")
            self.assertEqual(fix_record.defect_id, "DEFECT-001")
            self.assertIn(str(target_file), fix_record.files_modified)
            self.assertEqual(fix_record.regression_tests_added, ["test_add_correct_sum"])
            self.assertEqual(target_file.read_text(encoding="utf-8").strip(), "def add(a, b): return a + b")

    def test_independent_validator_evaluates_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            validator = IndependentValidator(root)

            defect = DefectReport(
                defect_id="DEF-100",
                title="Test defect",
                severity=Severity.MEDIUM,
                component="comp",
                file_path="path",
                line_number=None,
                reproduction_steps=[],
                expected_behavior="",
                actual_behavior="",
                confidence=1.0,
            )
            fix = FixRecord(
                defect_id="DEF-100",
                worktree_or_branch="branch",
                files_modified=[],
                diff="",
                regression_tests_added=[],
            )

            # Test passing command (python -c "exit(0)")
            val_pass = validator.validate(defect, fix, ["python3", "-c", "print('Ran 3 tests in 0.01s'); exit(0)"])
            self.assertEqual(val_pass.verdict, Verdict.PASS)
            self.assertFalse(val_pass.regressions_detected)
            self.assertEqual(val_pass.tests_run, 3)
            self.assertEqual(val_pass.tests_passed, 3)

            # Test failing command (python -c "exit(1)")
            val_fail = validator.validate(defect, fix, ["python3", "-c", "print('FAIL: test'); exit(1)"])
            self.assertEqual(val_fail.verdict, Verdict.FAIL)
            self.assertTrue(val_fail.regressions_detected)

    def test_end_to_end_autonomous_qa_self_healing_cycle(self):
        """Proof-of-concept: Injected defect is detected, repaired, and validated autonomously."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            orchestrator = AutonomousQAOrchestrator(root)

            # 1. Create a buggy module and test script
            module_file = root / "service.py"
            module_file.write_text("def get_status(): return 'stale_running'\n", encoding="utf-8")

            test_file = root / "test_service.py"
            test_file.write_text(
                "import unittest\n"
                "from service import get_status\n\n"
                "class ServiceTests(unittest.TestCase):\n"
                "    def test_status(self):\n"
                "        self.assertEqual(get_status(), 'completed')\n\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n",
                encoding="utf-8",
            )

            test_cmd = ["python3", "-m", "unittest", "test_service.py"]

            # 2. Patch function that Fixer will execute
            def patch_fn(workspace: Path) -> list[str]:
                target = workspace / "service.py"
                target.write_text("def get_status(): return 'completed'\n", encoding="utf-8")
                return [str(target)]

            # 3. Execute closed-loop cycle
            decision_logs = orchestrator.execute_remediation_cycle(
                test_runner_cmd=test_cmd,
                patch_fn=patch_fn,
                regression_test_name="test_status_must_return_completed",
            )

            # 4. Verify decision log and final state
            self.assertGreaterEqual(len(decision_logs), 1)
            final_log = decision_logs[-1]
            self.assertEqual(final_log.verdict, Verdict.PASS)
            self.assertIn("DEFECT-test_status", final_log.defect_id)
            self.assertEqual(module_file.read_text(encoding="utf-8").strip(), "def get_status(): return 'completed'")


if __name__ == "__main__":
    unittest.main()
