import json
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from pothole.repair.outcome import RegressionCase, RepairOutcome
from pothole.repair.sink import GitHubSink, LocalSink
from pothole.spec import engine
from pothole.spec.model import PipelineSpec
from pothole.spec.ops import MapField

DAY = date(2026, 11, 8)
RENAME = MapField(column="agency", source="agency_name")


@pytest.fixture
def outcome(spec: PipelineSpec, failing: Any) -> RepairOutcome:
    from pothole.repair.outcome import table_rows

    rows = failing.rows[:4]
    patched = spec.model_copy(deep=True)
    patched.column("agency").source = "agency_name"
    case = RegressionCase(rows, table_rows(engine.run(patched, rows, [], failing.now).table))
    return RepairOutcome("patch", [RENAME], case, "The source renamed agency.")


@pytest.fixture
def results(spec: PipelineSpec, failing: Any):
    return engine.run(spec, failing.rows, [], failing.now).checks


def test_local_sink_writes_a_pull_request_and_an_issue(tmp_path, spec, outcome, results):
    sink = LocalSink(tmp_path / "out")
    pr = sink.open_pr(outcome, spec, results, [], day=DAY, drill=True, verdict="matches")
    root = Path(pr.ref)
    assert pr.branch == "drill/2026-11-08-1"
    assert "agency_name" in (root / "pipelines" / "311.yaml").read_text()
    body = (root / "PR.md").read_text()
    assert "Drill" in body and "Verdict: matches" in body and "required" in body
    case = json.loads(next((root / "tests" / "regression").glob("*.json")).read_text())
    assert case["spec"] == "pipelines/311.yaml" and len(case["rows"]) == 4
    second = sink.open_pr(outcome, spec, results, [], day=DAY)
    assert second.branch == "repair/2026-11-08-2"
    assert "proposed, not judged" in (Path(second.ref) / "PR.md").read_text()
    issue = sink.open_issue("Source down", results, day=DAY)
    assert "Source down" in Path(issue.ref).read_text()


class Remote:
    """Real git against a local origin; `gh` and `uv` are recorded or stubbed."""

    def __init__(self, tmp: Path, checks_pass: bool) -> None:
        self.checks_pass = checks_pass
        self.calls: list[list[str]] = []
        self.bodies: list[str] = []
        self.origin = tmp / "origin.git"
        self.repo = tmp / "repo"
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.repo)], check=True)
        (self.repo / "pipelines").mkdir()
        (self.repo / "pipelines" / "311.yaml").write_text("old\n")
        subprocess.run([*git, "-C", str(self.repo), "add", "."], check=True)
        subprocess.run([*git, "-C", str(self.repo), "commit", "-q", "-m", "start"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "push", "-q", "origin", "HEAD:main"], check=True
        )

    def main_sha(self) -> str:
        out = subprocess.run(["git", "-C", str(self.origin), "rev-parse", "main"],
                             capture_output=True, text=True, check=True)  # fmt: skip
        return out.stdout.strip()

    def branches(self) -> list[str]:
        out = subprocess.run(["git", "-C", str(self.origin), "branch"],
                             capture_output=True, text=True, check=True)  # fmt: skip
        return [b.strip("* ").strip() for b in out.stdout.splitlines()]

    def __call__(self, args: list[str], cwd: Path, env: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        assert env["GH_TOKEN"] == "tok"
        if args[0] == "git":
            return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
        if args[0] == "uv":
            ok = self.checks_pass or "pytest" not in args
            return subprocess.CompletedProcess(
                args, 0 if ok else 1, "1 passed\n" if ok else "1 failed\n", ""
            )
        if args[:3] == ["gh", "pr", "create"] or args[:3] == ["gh", "issue", "create"]:
            self.bodies.append(Path(args[args.index("--body-file") + 1]).read_text())
            return subprocess.CompletedProcess(args, 0, f"https://github.com/o/r/{args[1]}/1\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")


def sink_for(remote: Remote) -> GitHubSink:
    return GitHubSink("o/r", "tok", remote.repo, runner=remote)  # type: ignore[arg-type]


def test_pull_request_is_on_a_branch_with_label_verdict_and_sha(tmp_path, spec, outcome, results):
    remote = Remote(tmp_path, checks_pass=True)
    before = remote.main_sha()
    filed = sink_for(remote).open_pr(
        outcome, spec, results, [], day=DAY, drill=True, verdict="exact"
    )
    assert filed.kind == "pr" and filed.branch == "drill/2026-11-08-1"
    assert remote.branches() == ["drill/2026-11-08-1", "main"] and remote.main_sha() == before
    created = next(c for c in remote.calls if c[:3] == ["gh", "pr", "create"])
    assert created[created.index("--base") + 1] == "main"
    assert created[created.index("--label") + 1] == "drill"
    body = remote.bodies[0]
    sha = subprocess.run(["git", "-C", str(remote.origin), "rev-parse", "drill/2026-11-08-1"],
                         capture_output=True, text=True, check=True).stdout.strip()  # fmt: skip
    assert f"`{sha}`" in body and "Verdict: exact" in body and "1 passed" in body
    pushes = [c for c in remote.calls if c[:2] == ["git", "push"]]
    assert pushes == [["git", "push", "origin", "drill/2026-11-08-1:refs/heads/drill/2026-11-08-1"]]
    # a second pull request on the same day takes the next number
    again = sink_for(remote).open_pr(outcome, spec, results, [], day=DAY)
    assert again.branch == "repair/2026-11-08-1"
    labels = [c[c.index("--label") + 1] for c in remote.calls if c[:3] == ["gh", "pr", "create"]]
    assert labels[-1] == "proposed, not judged"


def test_bad_patch_on_branch_becomes_issue(tmp_path, spec, outcome, results):
    remote = Remote(tmp_path, checks_pass=False)
    before = remote.main_sha()
    filed = sink_for(remote).open_pr(outcome, spec, results, [], day=DAY)
    assert filed.kind == "issue" and "/issue/" in filed.ref
    assert not any(
        c[:2] == ["git", "push"] or c[:3] == ["gh", "pr", "create"] for c in remote.calls
    )
    assert remote.branches() == ["main"] and remote.main_sha() == before
    assert "1 failed" in remote.bodies[0] and "failed its own tests" in remote.bodies[0]
    local = subprocess.run(
        ["git", "-C", str(remote.repo), "branch"], capture_output=True, text=True
    )
    assert "repair/" not in local.stdout


def test_outage_issue_has_reason_and_failing_checks(tmp_path, results):
    remote = Remote(tmp_path, checks_pass=True)
    filed = sink_for(remote).open_issue("The source did not answer.", results, day=DAY)
    assert filed.kind == "issue" and "did not answer" in remote.bodies[0]
    assert "required" in remote.bodies[0]
