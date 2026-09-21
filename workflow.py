from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ReviewState(Enum):
    PENDING = "pending"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"


class CheckMode(Enum):
    OFF = "off"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class PreMergeCheck:
    name: str
    mode: CheckMode
    passing: bool = True

    def blocks_merge(self, request_changes_workflow: bool) -> bool:
        """Only an ERROR-mode check can block merge, and only when the
        request-changes workflow is enabled (per CodeRabbit docs)."""
        return (
            request_changes_workflow
            and self.mode == CheckMode.ERROR
            and not self.passing
        )


@dataclass
class ReviewThread:
    comment: str
    resolved: bool = False


@dataclass
class PullRequest:
    title: str
    request_changes_workflow: bool = True
    threads: list[ReviewThread] = field(default_factory=list)
    checks: list[PreMergeCheck] = field(default_factory=list)
    state: ReviewState = ReviewState.PENDING
    latest_commit_reviewed: bool = False
    overridden: bool = False

    # ---- narration helpers -------------------------------------------------
    def _log(self, msg: str) -> None:
        print(f"  {msg}")

    def merge_box_status(self) -> str:
        if self.state == ReviewState.APPROVED or self.overridden:
            return "MERGEABLE"
        if self.state == ReviewState.CHANGES_REQUESTED:
            return "BLOCKED (changes requested)"
        return "PENDING REVIEW"

    # ---- the workflow itself ------------------------------------------------
    def coderabbit_reviews_commit(self, actionable_comments: list[str]) -> None:
        print(f"\n[1] CodeRabbit reviews the latest commit of '{self.title}'")
        self.latest_commit_reviewed = True
        self.threads = [ReviewThread(c) for c in actionable_comments]

        if self.threads:
            self.state = ReviewState.CHANGES_REQUESTED
            self._log(f"Found {len(self.threads)} actionable comment(s):")
            for t in self.threads:
                self._log(f"   - {t.comment}")
            self._log("-> Submitting a REQUEST CHANGES review.")
        else:
            self._log("No actionable comments found.")
            self.try_approve()

    def push_fix(self, resolves: list[str]) -> None:
        print(f"\n[2] Author pushes a new commit addressing: {resolves}")
        self.latest_commit_reviewed = False  # new commit needs a fresh review
        for t in self.threads:
            if t.comment in resolves:
                t.resolved = True

    def coderabbit_reviews_again(self) -> None:
        print("\n[3] CodeRabbit re-reviews the new commit and resolves addressed threads")
        self.latest_commit_reviewed = True
        for t in self.threads:
            status = "resolved" if t.resolved else "still open"
            self._log(f"   - {t.comment}: {status}")
        self.try_approve()

    def set_check_result(self, name: str, passing: bool) -> None:
        for c in self.checks:
            if c.name == name:
                c.passing = passing
                print(f"\n[Pre-Merge Check] '{name}' -> {'PASS' if passing else 'FAIL'} ({c.mode.value} mode)")
                return
        raise ValueError(f"No such check: {name}")

    def try_approve(self) -> None:
        print("\n[4] CodeRabbit checks approval requirements")
        unresolved = [t for t in self.threads if not t.resolved]
        blocking_checks = [c for c in self.checks if c.blocks_merge(self.request_changes_workflow)]

        self._log(f"Latest commit reviewed: {self.latest_commit_reviewed}")
        self._log(f"Unresolved threads: {len(unresolved)}")
        self._log(f"Blocking (error-mode, failing) checks: {[c.name for c in blocking_checks]}")

        if not self.latest_commit_reviewed:
            self.state = ReviewState.CHANGES_REQUESTED
            self._log("-> Approval PENDING: latest commit not yet reviewed.")
            return
        if unresolved:
            self.state = ReviewState.CHANGES_REQUESTED
            self._log("-> Approval PENDING: unresolved review threads remain.")
            return
        if blocking_checks:
            self.state = ReviewState.CHANGES_REQUESTED
            self._log("-> Approval PENDING: an error-mode pre-merge check is failing.")
            return

        self.state = ReviewState.APPROVED
        self._log("[5] All requirements met -> CodeRabbit APPROVES the pull request.")

    def override_blocked_checks(self, actor: str, is_requested_reviewer: bool,
                                 override_requested_reviewers_only: bool = True) -> None:
        print(f"\n[Override attempt] {actor} tries to ignore failed checks")
        if override_requested_reviewers_only and not is_requested_reviewer:
            self._log(f"-> DENIED: {actor} is not a requested reviewer; override restricted.")
            return
        self.overridden = True
        self._log(f"-> {actor} overrides failing checks. PR is unblocked (tagged [IGNORED]).")


def run_demo() -> None:
    pr = PullRequest(title="feat: add pricing endpoint")
    pr.checks = [
        PreMergeCheck("docstrings", CheckMode.ERROR, passing=True),
        PreMergeCheck("no_todo_placeholders", CheckMode.ERROR, passing=True),
        PreMergeCheck("issue_assessment", CheckMode.WARNING, passing=False),
    ]

    print("=" * 70)
    print("DEMO: request_changes_workflow + error-mode Pre-Merge Checks")
    print("=" * 70)

    # Step 1: initial review finds actionable feedback
    pr.coderabbit_reviews_commit(actionable_comments=[
        "Add null check for `price` before formatting",
        "Missing docstring on `calculate_discount()`",
    ])
    print(f"\nMerge box: {pr.merge_box_status()}")

    # A failing error-mode check, independent of the review comments
    pr.set_check_result("docstrings", passing=False)
    pr.try_approve()
    print(f"\nMerge box: {pr.merge_box_status()}")

    # Step 2 & 3: author fixes the code review comments only
    pr.push_fix(resolves=["Add null check for `price` before formatting"])
    pr.coderabbit_reviews_again()
    print(f"\nMerge box: {pr.merge_box_status()}")

    # Fix the remaining thread
    pr.push_fix(resolves=["Missing docstring on `calculate_discount()`"])
    pr.coderabbit_reviews_again()
    print(f"\nMerge box: {pr.merge_box_status()}")

    # Docstring check still failing in error mode -> still blocked
    print("\n(Note: threads are clear, but the docstrings check is still failing)")

    # Demonstrate the restricted override
    pr.override_blocked_checks("pr-author", is_requested_reviewer=False)
    print(f"Merge box: {pr.merge_box_status()}")

    pr.override_blocked_checks("requested-reviewer-jane", is_requested_reviewer=True)
    print(f"Merge box: {pr.merge_box_status()}")

    print("\n" + "=" * 70)
    print("Alternate ending: check passes normally instead of being overridden")
    print("=" * 70)

    pr2 = PullRequest(title="fix: correct tax rounding")
    pr2.checks = [PreMergeCheck("docstrings", CheckMode.ERROR, passing=False)]
    pr2.coderabbit_reviews_commit(actionable_comments=["Round to 2 decimal places, not 0"])
    print(f"\nMerge box: {pr2.merge_box_status()}")

    pr2.push_fix(resolves=["Round to 2 decimal places, not 0"])
    pr2.coderabbit_reviews_again()
    print(f"\nMerge box: {pr2.merge_box_status()}")

    pr2.set_check_result("docstrings", passing=True)
    pr2.try_approve()
    print(f"\nMerge box: {pr2.merge_box_status()}")


if __name__ == "__main__":
    run_demo()
