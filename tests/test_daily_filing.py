"""What the run records is what was actually filed."""

from datetime import date

import pytest

from pothole import dailyai
from pothole.repair.outcome import RepairOutcome
from pothole.repair.sink import Filed

DAY = date(2026, 11, 15)


class FailedPatchSink:
    """A sink whose gates or pull request creation failed: the patch became an issue."""

    def open_pr(self, *a: object, **k: object) -> Filed:
        return Filed("issue", "https://github.com/o/r/issues/1")

    def open_issue(self, *a: object, **k: object) -> Filed:  # pragma: no cover - not used
        raise AssertionError("a patch is filed through open_pr")


@pytest.mark.parametrize("drill", [False, True])
def test_a_patch_that_ended_as_an_issue_is_recorded_as_an_issue(drill: bool) -> None:
    record = dailyai._file(
        FailedPatchSink(),  # type: ignore[arg-type]
        RepairOutcome("patch"),
        None,  # type: ignore[arg-type]
        [],
        [],
        DAY,
        drill,
        "correct: x" if drill else None,
    )
    assert record.action == "issue" and record.url == "https://github.com/o/r/issues/1"
    assert record.not_judged is False
