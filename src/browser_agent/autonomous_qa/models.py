"""Data models for Autonomous QA and Self-Healing Development Loop."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"


@dataclass
class DefectReport:
    defect_id: str
    title: str
    severity: Severity
    component: str
    file_path: str
    line_number: int | None
    reproduction_steps: list[str]
    expected_behavior: str
    actual_behavior: str
    confidence: float  # 0.0 - 1.0
    stack_trace: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


@dataclass
class FixRecord:
    defect_id: str
    worktree_or_branch: str
    files_modified: list[str]
    diff: str
    regression_tests_added: list[str]
    applied_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


@dataclass
class ValidationResult:
    defect_id: str
    verdict: Verdict
    evidence: str
    tests_run: int
    tests_passed: int
    regressions_detected: bool
    validated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


@dataclass
class CycleDecisionLog:
    cycle_id: str
    iteration: int
    defect_id: str
    action_taken: str
    verdict: Verdict
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
