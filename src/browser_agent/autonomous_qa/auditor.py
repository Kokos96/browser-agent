"""QA Auditor: Read-only defect identification, reproduction, and evidence gathering."""

import json
import re
import subprocess
from pathlib import Path
from typing import Callable

from browser_agent.autonomous_qa.models import DefectReport, Severity


class QAAuditor:
    """Read-only QA Auditor. Inspects code, executes tests, and reports defects."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root

    def scan_for_defects(self, test_runner_cmd: list[str]) -> list[DefectReport]:
        """Execute test suite in read-only mode and capture failures as structured DefectReports."""
        defects = []
        try:
            res = subprocess.run(
                test_runner_cmd,
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                report = self._parse_failure(res.stdout + "\n" + res.stderr)
                if report:
                    defects.append(report)
        except Exception as exc:
            defects.append(
                DefectReport(
                    defect_id="AUDIT-EXC-001",
                    title="Audit execution failed with exception",
                    severity=Severity.HIGH,
                    component="test_runner",
                    file_path=str(self.workspace_root),
                    line_number=None,
                    reproduction_steps=[f"Run command: {' '.join(test_runner_cmd)}"],
                    expected_behavior="Test runner executes cleanly",
                    actual_behavior=str(exc),
                    confidence=1.0,
                    stack_trace=str(exc),
                )
            )
        return defects

    def _parse_failure(self, output: str) -> DefectReport | None:
        """Parse Python unittest or pytest failure output into a DefectReport."""
        # Check unittest ERROR/FAIL pattern
        match = re.search(r"FAIL:\s*(\w+)\s*\(([\w\.]+)\)", output)
        if not match:
            match = re.search(r"ERROR:\s*(\w+)\s*\(([\w\.]+)\)", output)

        if match:
            test_name = match.group(1)
            test_class = match.group(2)
            return DefectReport(
                defect_id=f"DEFECT-{test_name}",
                title=f"Test failure in {test_class}.{test_name}",
                severity=Severity.HIGH,
                component=test_class,
                file_path=str(self.workspace_root / "tests"),
                line_number=None,
                reproduction_steps=[f"python -m unittest {test_class}.{test_name}"],
                expected_behavior=f"Assertion in {test_name} should succeed",
                actual_behavior="Test raised AssertionError or Exception during execution",
                confidence=0.95,
                stack_trace=output[-2000:],
            )

        # Fallback generic failure report
        return DefectReport(
            defect_id="DEFECT-GENERIC-001",
            title="Automated test suite returned non-zero exit code",
            severity=Severity.MEDIUM,
            component="test_suite",
            file_path=str(self.workspace_root),
            line_number=None,
            reproduction_steps=["Run test command"],
            expected_behavior="All tests pass",
            actual_behavior="Non-zero exit code detected",
            confidence=0.90,
            stack_trace=output[-2000:],
        )
