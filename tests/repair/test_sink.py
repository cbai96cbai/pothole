import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from pothole.repair import render
from pothole.repair.outcome import RegressionCase, RepairOutcome, table_rows
from pothole.repair.sink import GitHubSink, LocalSink
from pothole.spec import engine
from pothole.spec.model import PipelineSpec
from pothole.spec.ops import MapField

DAY = date(2026, 11, 8)
ROOT = Path(__file__).parents[2]


def make_outcome(spec: PipelineSpec, failing: Any, field: str, reason: str = "Renamed.") -> Any:
    """A patch for `agency` read from `field`, with a case of four rows that carry coordinates."""
    renamed = [
        {("agency" if k == "agency_name" else k): v for k, v in r.items()} for r in failing.rows
    ]
    rows = [
        {(field if k == "agency" else k): v for k, v in r.items()}
        for r in renamed
        if r.get("latitude") and r.get("longitude")
    ][:4]
    patched = spec.model_copy(deep=True)
    patched.column("agency").source = field
    now = failing.now
    case = RegressionCase(rows, table_rows(engine.run(patched, rows, [], now).table))
    return RepairOutcome("patch", [MapField(column="agency", source=field)], case, reason)


@pytest.fixture
def outcome(spec: PipelineSpec, failing: Any) -> RepairOutcome:
    return make_outcome(spec, failing, "agency_name")


@pytest.fixture
def results(spec: PipelineSpec, failing: Any):
    return engine.run(spec, failing.rows, [], failing.now).checks


def test_local_sink_writes_a_pull_request_and_an_issue(tmp_path, spec, outcome, results):
    sink = LocalSink(tmp_path / "out")
    pr = sink.open_pr(outcome, spec, results, [], day=DAY, drill=True, verdict="matches")
    root = Path(pr.ref)
    assert re.fullmatch(r"drill/2026-11-08-[0-9a-f]{6}", pr.branch or "")
    assert "agency_name" in (root / render.SPEC_PATH).read_text()
    body = (root / "PR.md").read_text()
    assert "Drill" in body and "matches" in body and "required" in body
    case = json.loads(next((root / "tests" / "regression").glob("*.json")).read_text())
    assert case["spec"] == render.SPEC_PATH and len(case["rows"]) == 4
    second = sink.open_pr(outcome, spec, results, [], day=DAY)
    assert "Proposed, not judged" in (Path(second.ref) / "PR.md").read_text()
    issue = sink.open_issue("Source down", results, day=DAY)
    assert "Source down" in Path(issue.ref).read_text()


def test_model_text_cannot_close_its_block_or_mention_anyone(outcome, results):
    hostile = "done ``` @everyone [x](http://y) ````"
    body = render.pr_body(
        RepairOutcome("patch", outcome.ops, outcome.regression, hostile),
        results, [], drill=False, verdict=None, sha="abc", tests="ok",
    )  # fmt: skip
    fence = "`````"
    assert f"{fence}\n{hostile}\n{fence}" in body


GATE_SOURCE = """\
from pathlib import Path

import pothole


def test_gates_import_the_branch_own_source():
    assert Path(pothole.__file__).resolve().is_relative_to(Path(__file__).parents[1].resolve())
"""
CHECK_SPEC_CMD = (
    "from pathlib import Path; import pothole; "
    "here = Path.cwd().resolve(); "
    "assert Path(pothole.__file__).resolve().is_relative_to(here), pothole.__file__; "
    "from pothole.cli import app; app()"
)


class Remote:
    """Real git against a local origin; `gh` is recorded. Gates are stubbed or really run."""

    def __init__(self, tmp: Path, gates: str = "pass", push_fails: bool = False) -> None:
        self.gates, self.push_fails = gates, push_fails
        self.calls: list[list[str]] = []
        self.bodies: list[str] = []
        self.origin = tmp / "origin.git"
        self.repo = tmp / "repo"
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.repo)], check=True)
        (self.repo / "pipelines").mkdir()
        shutil.copy(ROOT / "fixtures/batches/spec.yaml", self.repo / render.SPEC_PATH)
        (self.repo / "tests" / "regression").mkdir(parents=True)
        shutil.copy(ROOT / "tests/regression/test_cases.py", self.repo / "tests/regression/")
        shutil.copytree(
            ROOT / "src", self.repo / "src", ignore=shutil.ignore_patterns("__pycache__")
        )
        (self.repo / "tests" / "test_gate_source.py").write_text(GATE_SOURCE)
        subprocess.run([*git, "-C", str(self.repo), "add", "."], check=True)
        subprocess.run([*git, "-C", str(self.repo), "commit", "-q", "-m", "start"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "push", "-q", "origin", "HEAD:main"], check=True
        )

    def git_out(self, *args: str, where: Path | None = None) -> str:
        out = subprocess.run(["git", "-C", str(where or self.origin), *args],
                             capture_output=True, text=True, check=True)  # fmt: skip
        return out.stdout.strip()

    def branches(self) -> list[str]:
        return [b.strip("* ") for b in self.git_out("branch").splitlines()]

    def __call__(self, args: list[str], cwd: Path, env: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        assert env["GH_TOKEN"] == "tok"
        if args[:2] == ["git", "push"] and self.push_fails and "--delete" not in args:
            return subprocess.CompletedProcess(args, 1, "", "rejected")
        if args[0] == "git":
            return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
        if args[0] == "uv":  # the two gates
            if self.gates == "real":
                args = self.real(args[2:])
                return subprocess.run(
                    args, cwd=cwd, env=env, capture_output=True, text=True, check=False
                )
            else:
                failed = self.gates == "fail" and args[2] == "pytest"
                return subprocess.CompletedProcess(
                    args, int(failed), "1 failed\n" if failed else "1 passed\n", ""
                )
            return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
        if args[:3] in (["gh", "pr", "create"], ["gh", "issue", "create"]):
            self.bodies.append(Path(args[args.index("--body-file") + 1]).read_text())
            return subprocess.CompletedProcess(args, 0, f"https://github.com/o/r/{args[1]}/1\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    @staticmethod
    def real(rest: list[str]) -> list[str]:
        if rest[0] == "pytest":
            return [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *rest[1:]]
        return [sys.executable, "-c", CHECK_SPEC_CMD, *rest[1:]]


def sink_for(remote: Remote) -> GitHubSink:
    return GitHubSink("o/r", "tok", remote.repo, runner=remote)  # type: ignore[arg-type]


def test_pull_request_is_on_a_branch_with_labels_verdict_and_sha(tmp_path, spec, outcome, results):
    remote = Remote(tmp_path)
    before = remote.git_out("rev-parse", "main")
    filed = sink_for(remote).open_pr(
        outcome, spec, results, [], day=DAY, drill=True, verdict="exact"
    )
    assert filed.kind == "pr" and re.fullmatch(r"drill/2026-11-08-[0-9a-f]{6}", filed.branch or "")
    assert remote.branches() == [filed.branch, "main"]
    assert remote.git_out("rev-parse", "main") == before
    created = next(c for c in remote.calls if c[:3] == ["gh", "pr", "create"])
    assert created[created.index("--base") + 1] == "main"
    assert [created[i + 1] for i, a in enumerate(created) if a == "--label"] == ["drill"]
    assert ["gh", "label", "create", "drill", "--repo", "o/r", "--force"] in remote.calls
    sha = remote.git_out("rev-parse", filed.branch or "")
    body = remote.bodies[0]
    assert f"`{sha}`" in body and "exact" in body and "1 passed" in body
    assert [c for c in remote.calls if c[:2] == ["git", "push"]] == [
        ["git", "push", "origin", f"{filed.branch}:refs/heads/{filed.branch}"]
    ]
    real = sink_for(remote).open_pr(outcome, spec, results, [], day=DAY)
    assert real.branch != filed.branch and (real.branch or "").startswith("repair/")
    labels = [c[i + 1] for c in remote.calls if c[:3] == ["gh", "pr", "create"]
              for i, a in enumerate(c) if a == "--label"]  # fmt: skip
    assert labels[-2:] == ["repair", "not-judged"]
    assert "Proposed, not judged" in remote.bodies[-1]


def test_bad_patch_on_branch_becomes_issue(tmp_path, spec, outcome, results):
    remote = Remote(tmp_path, gates="fail")
    before = remote.git_out("rev-parse", "main")
    filed = sink_for(remote).open_pr(outcome, spec, results, [], day=DAY)
    assert filed.kind == "issue" and "/issue/" in filed.ref
    assert not any(
        c[:2] == ["git", "push"] or c[:3] == ["gh", "pr", "create"] for c in remote.calls
    )
    assert remote.branches() == ["main"] and remote.git_out("rev-parse", "main") == before
    assert "1 failed" in remote.bodies[0] and "failed its own tests" in remote.bodies[0]
    assert "repair/" not in remote.git_out("branch", where=remote.repo)


def test_a_failed_push_becomes_an_issue(tmp_path, spec, outcome, results):
    remote = Remote(tmp_path, push_fails=True)
    filed = sink_for(remote).open_pr(outcome, spec, results, [], day=DAY)
    assert filed.kind == "issue" and "Could not open the pull request" in remote.bodies[0]
    assert not any(c[:3] == ["gh", "pr", "create"] for c in remote.calls)
    assert remote.branches() == ["main"]


def test_both_gates_really_run_for_a_rename_to_a_field_the_old_fixtures_lack(
    tmp_path, spec, failing, results
):
    new = make_outcome(spec, failing, "agency_zz")
    remote = Remote(tmp_path, gates="real")
    filed = sink_for(remote).open_pr(new, spec, results, [], day=DAY)
    assert filed.kind == "pr", remote.bodies
    body = remote.bodies[0]
    assert "pytest -q" in body and "check-spec" in body and "ok" in body
    tree = remote.git_out("ls-tree", "-r", "--name-only", filed.branch or "")
    assert "tests/regression/batches/" in tree and render.SPEC_PATH in tree
    assert "agency_zz" in remote.git_out("show", f"{filed.branch}:{render.SPEC_PATH}")


def test_outage_issue_has_reason_and_failing_checks(tmp_path, results):
    remote = Remote(tmp_path)
    filed = sink_for(remote).open_issue("The source did not answer.", results, day=DAY)
    assert filed.kind == "issue" and "did not answer" in remote.bodies[0]
    assert "required" in remote.bodies[0]
