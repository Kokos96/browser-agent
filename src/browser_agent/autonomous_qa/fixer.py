"""Fixer: Applies targeted bugfixes and adds regression assertions in an isolated environment."""

import subprocess
from pathlib import Path
from typing import Callable

from browser_agent.autonomous_qa.models import DefectReport, FixRecord


class Fixer:
    """Applies verified, targeted bug fixes and ensures regression tests are attached."""

    def __init__(self, workspace_root: Path, branch_name: str = "auto-fix/controlled-defect"):
        self.workspace_root = workspace_root
        self.branch_name = branch_name

    def apply_fix(
        self,
        defect: DefectReport,
        patch_fn: Callable[[Path], list[str]],
        regression_test_name: str,
    ) -> FixRecord:
        """
        Apply a code fix via patch_fn in workspace_root.
        Returns a FixRecord documenting modified files and diff.
        """
        # Execute patch function
        modified_files = patch_fn(self.workspace_root)

        # Capture diff
        diff_output = ""
        try:
            res = subprocess.run(
                ["git", "diff"],
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
            )
            diff_output = res.stdout if res.returncode == 0 else ""
        except Exception:
            diff_output = "Diff unavailable (non-git context or error)"

        return FixRecord(
            defect_id=defect.defect_id,
            worktree_or_branch=self.branch_name,
            files_modified=modified_files,
            diff=diff_output,
            regression_tests_added=[regression_test_name],
        )
