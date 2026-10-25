"""Independent Validator: Verifies fixes independently with zero trust in fixers."""

import subprocess
from pathlib import Path

from browser_agent.autonomous_qa.models import (
    DefectReport,
    FixRecord,
    ValidationResult,
    Verdict,
)


class IndependentValidator:
    """Independent verification agent. Evaluates fix records and test suites without bias."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root

    def validate(
        self,
        defect: DefectReport,
        fix: FixRecord,
        test_runner_cmd: list[str],
    ) -> ValidationResult:
        """
        Independently execute the test suite after fix application.
        Returns a ValidationResult with PASS, FAIL, or BLOCKED.
        """
        try:
            res = subprocess.run(
                test_runner_cmd,
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
            )
            output = res.stdout + "\n" + res.stderr
            all_passed = (res.returncode == 0)

            # Check if regressions were detected
            regressions = not all_passed

            # Parse count of tests
            # Typical unittest output: "Ran X tests in Ys"
            import re
            tests_run = 0
            tests_passed = 0
            match = re.search(r"Ran (\d+) tests", output)
            if match:
                tests_run = int(match.group(1))
                if all_passed:
                    tests_passed = tests_run

            verdict = Verdict.PASS if all_passed else Verdict.FAIL
            evidence = (
                f"Validation command returned exit code {res.returncode}. Output excerpt: {output[-1000:].strip()}"
            )

            return ValidationResult(
                defect_id=defect.defect_id,
                verdict=verdict,
                evidence=evidence,
                tests_run=tests_run,
                tests_passed=tests_passed,
                regressions_detected=regressions,
            )
        except Exception as exc:
            return ValidationResult(
                defect_id=defect.defect_id,
                verdict=Verdict.BLOCKED,
                evidence=f"Validation process encountered an operational blocker: {exc}",
                tests_run=0,
                tests_passed=0,
                regressions_detected=False,
            )
