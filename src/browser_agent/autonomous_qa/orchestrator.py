"""Autonomous QA Orchestrator: Manages the closed-loop defect lifecycle."""

import uuid
from pathlib import Path
from typing import Callable

from browser_agent.autonomous_qa.auditor import QAAuditor
from browser_agent.autonomous_qa.fixer import Fixer
from browser_agent.autonomous_qa.models import (
    CycleDecisionLog,
    DefectReport,
    FixRecord,
    Severity,
    ValidationResult,
    Verdict,
)
from browser_agent.autonomous_qa.validator import IndependentValidator


class AutonomousQAOrchestrator:
    """
    Central loop coordinator.
    Enforces maximum iterations, maintains the decision log, and enforces quality gates.
    """

    MAX_ITERATIONS = 5

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root
        self.auditor = QAAuditor(workspace_root)
        self.fixer = Fixer(workspace_root)
        self.validator = IndependentValidator(workspace_root)
        self.decision_logs: list[CycleDecisionLog] = []

    def execute_remediation_cycle(
        self,
        test_runner_cmd: list[str],
        patch_fn: Callable[[Path], list[str]],
        regression_test_name: str,
    ) -> list[CycleDecisionLog]:
        """
        Execute an end-to-end self-healing cycle:
        1. Auditor finds defects.
        2. Orchestrator queues by severity.
        3. Fixer applies patch and attaches regression test.
        4. Independent Validator verifies fix.
        5. Decision log is updated.
        """
        cycle_id = f"CYCLE-{uuid.uuid4().hex[:8]}"

        # Step 1: Audit
        defects = self.auditor.scan_for_defects(test_runner_cmd)
        if not defects:
            log = CycleDecisionLog(
                cycle_id=cycle_id,
                iteration=1,
                defect_id="NONE",
                action_taken="Initial audit passed cleanly. No defects found.",
                verdict=Verdict.PASS,
                details={"defects_count": 0},
            )
            self.decision_logs.append(log)
            return self.decision_logs

        # Step 2: Prioritize Queue (CRITICAL -> HIGH -> MEDIUM -> LOW)
        severity_order = {
            Severity.CRITICAL: 0,
            Severity.HIGH: 1,
            Severity.MEDIUM: 2,
            Severity.LOW: 3,
        }
        defects.sort(key=lambda d: severity_order.get(d.severity, 4))
        target_defect = defects[0]

        # Step 3: Iterate up to MAX_ITERATIONS
        for iteration in range(1, self.MAX_ITERATIONS + 1):
            # Apply fix
            fix_record = self.fixer.apply_fix(
                target_defect,
                patch_fn,
                regression_test_name,
            )

            # Validate independently
            val_result = self.validator.validate(
                target_defect,
                fix_record,
                test_runner_cmd,
            )

            # Record decision
            decision_log = CycleDecisionLog(
                cycle_id=cycle_id,
                iteration=iteration,
                defect_id=target_defect.defect_id,
                action_taken=f"Applied fix for {target_defect.title}. Added regression test '{regression_test_name}'",
                verdict=val_result.verdict,
                details={
                    "evidence": val_result.evidence,
                    "tests_run": val_result.tests_run,
                    "tests_passed": val_result.tests_passed,
                    "files_modified": fix_record.files_modified,
                },
            )
            self.decision_logs.append(decision_log)

            if val_result.verdict == Verdict.PASS:
                # Successfully healed
                break
            elif val_result.verdict == Verdict.BLOCKED:
                # Operational blocker, stop immediately
                break

        return self.decision_logs
