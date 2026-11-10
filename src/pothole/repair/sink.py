"""Where a decision goes: files on disk (LocalSink) or a pull request / issue (GitHubSink).

Nothing here touches `main`: a patch lives on a `repair/<date>-<n>` or `drill/<date>-<n>` branch,
and the pull request is opened for a person to merge.
"""

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pothole.repair import render
from pothole.repair.outcome import RepairOutcome
from pothole.spec import ops
from pothole.spec.checks import CheckResult
from pothole.spec.model import PipelineSpec

Runner = Callable[..., "subprocess.CompletedProcess[str]"]
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
CHECKS = (
    ["uv", "run", "pytest", "-q", "tests/regression"],
    [
        "uv", "run", "pothole", "check-spec", "--spec", render.SPEC_PATH,
        "--batches", "fixtures/batches",
    ],
)  # fmt: skip


@dataclass(frozen=True)
class Filed:
    kind: Literal["pr", "issue"]
    ref: str  # a URL or a path
    branch: str | None = None


def run_process(
    args: Sequence[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None
) -> "subprocess.CompletedProcess[str]":
    """Run a command, capturing text; the caller reads the return code."""
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, check=False)


def _name(drill: bool, day: date, n: int) -> str:
    return f"{'drill' if drill else 'repair'}/{day.isoformat()}-{n}"


class LocalSink:
    """Writes each pull request and issue as a folder or file under `directory`."""

    def __init__(self, directory: Path) -> None:
        self.dir = directory

    def _next(self, prefix: str) -> int:
        return len(list(self.dir.glob(f"{prefix}-*"))) + 1 if self.dir.exists() else 1

    def open_pr(
        self,
        outcome: RepairOutcome,
        spec: PipelineSpec,
        before: list[CheckResult],
        after: list[CheckResult],
        *,
        day: date,
        drill: bool = False,
        verdict: str | None = None,
    ) -> Filed:
        if outcome.action != "patch" or outcome.regression is None:
            raise ValueError("a pull request needs a patch outcome with a regression case")
        branch = _name(drill, day, self._next("pr"))
        root = self.dir / f"pr-{branch.replace('/', '-')}"
        (root / "tests" / "regression").mkdir(parents=True)
        (root / "pipelines").mkdir()
        (root / render.SPEC_PATH).write_text(ops.apply(spec, outcome.ops).to_yaml())
        case_file = root / "tests" / "regression" / f"{branch.replace('/', '-')}.json"
        case_file.write_text(render.regression_file(outcome.regression))
        body = render.pr_body(
            outcome, before, after, drill=drill, verdict=verdict, sha=None, tests=None
        )
        (root / "PR.md").write_text(f"# {render.pr_title(outcome, drill)}\n\n{body}")
        return Filed("pr", str(root), branch)

    def open_issue(
        self, reason: str, failing: list[CheckResult], *, day: date, drill: bool = False
    ) -> Filed:
        path = self.dir / f"issue-{day.isoformat()}-{self._next('issue')}.md"
        self.dir.mkdir(parents=True, exist_ok=True)
        title = "Drill: " if drill else ""
        path.write_text(f"# {title}{reason[:70]}\n\n{render.issue_body(reason, failing)}")
        return Filed("issue", str(path))


class GitHubSink:
    def __init__(
        self,
        repo: str,
        token: str,
        repo_dir: Path = Path("."),
        runner: Runner = run_process,
        base: str = "main",
    ) -> None:
        self.repo, self.repo_dir, self.runner, self.base = repo, repo_dir, runner, base
        self.env = {**os.environ, "GH_TOKEN": token, "GITHUB_TOKEN": token}

    def _run(
        self, args: Sequence[str], cwd: Path | None = None
    ) -> "subprocess.CompletedProcess[str]":
        return self.runner(list(args), cwd=cwd or self.repo_dir, env=self.env)

    def _must(self, args: Sequence[str], cwd: Path | None = None) -> str:
        done = self._run(args, cwd)
        if done.returncode != 0:
            raise RuntimeError(
                f"{' '.join(args[:3])} failed: {(done.stderr or done.stdout)[-500:]}"
            )
        return (done.stdout or "").strip()

    def _next_branch(self, drill: bool, day: date) -> str:
        self._must(["git", "fetch", "origin", self.base])
        prefix = _name(drill, day, 0).rsplit("-", 1)[0]
        listed = self._must(["git", "branch", "-r", "--list", f"origin/{prefix}-*"])
        return _name(drill, day, len([ln for ln in listed.splitlines() if ln.strip()]) + 1)

    def open_issue(
        self,
        reason: str,
        failing: list[CheckResult],
        *,
        day: date,
        drill: bool = False,
        output: str | None = None,
    ) -> Filed:
        title = (
            f"{'Drill: ' if drill else ''}{day.isoformat()}: {reason.strip().splitlines()[0][:70]}"
        )
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "body.md"
            body.write_text(render.issue_body(reason, failing, output))
            cmd = ["gh", "issue", "create", "--repo", self.repo, "--title", title]
            cmd += ["--body-file", str(body)]
            if drill:
                cmd += ["--label", "drill"]
            url = self._must(cmd)
        return Filed("issue", url)

    def open_pr(
        self,
        outcome: RepairOutcome,
        spec: PipelineSpec,
        before: list[CheckResult],
        after: list[CheckResult],
        *,
        day: date,
        drill: bool = False,
        verdict: str | None = None,
    ) -> Filed:
        if outcome.action != "patch" or outcome.regression is None:
            raise ValueError("a pull request needs a patch outcome with a regression case")
        branch = self._next_branch(drill, day)
        if not branch.startswith(("repair/", "drill/")):
            raise ValueError(f"refusing branch {branch!r}")
        work = Path(tempfile.mkdtemp(prefix="pothole-pr-")) / "tree"
        self._must(["git", "worktree", "add", "-b", branch, str(work), f"origin/{self.base}"])
        try:
            return self._propose(branch, work, outcome, spec, before, after, day, drill, verdict)
        finally:
            self._run(["git", "worktree", "remove", "--force", str(work)])
            self._run(["git", "branch", "-D", branch])
            shutil.rmtree(work.parent, ignore_errors=True)

    def _propose(
        self,
        branch: str,
        work: Path,
        outcome: RepairOutcome,
        spec: PipelineSpec,
        before: list[CheckResult],
        after: list[CheckResult],
        day: date,
        drill: bool,
        verdict: str | None,
    ) -> Filed:
        assert outcome.regression is not None
        (work / "pipelines").mkdir(exist_ok=True)
        (work / "tests" / "regression").mkdir(parents=True, exist_ok=True)
        (work / render.SPEC_PATH).write_text(ops.apply(spec, outcome.ops).to_yaml())
        case = work / "tests" / "regression" / f"{branch.replace('/', '-')}.json"
        case.write_text(render.regression_file(outcome.regression))
        self._must(["git", "add", render.SPEC_PATH, str(case.relative_to(work))], work)
        who = ["-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}"]
        message = render.pr_title(outcome, drill)
        self._must(["git", *who, "commit", "-m", message], work)
        sha = self._must(["git", "rev-parse", "HEAD"], work)
        output = ""
        for check in CHECKS:
            done = self._run(check, work)
            output += f"$ {' '.join(check)}\n{done.stdout}{done.stderr}\n"
            if done.returncode != 0:
                reason = (
                    f"The patch failed its own tests on {branch} ({sha[:10]}): {outcome.reason}"
                )
                return self.open_issue(reason, before, day=day, drill=drill, output=output)
        self._must(["git", "push", "origin", f"{branch}:refs/heads/{branch}"], work)
        label = "drill" if drill else render.NOT_JUDGED
        self._run(["gh", "label", "create", label, "--repo", self.repo, "--force"])
        body = render.pr_body(
            outcome, before, after, drill=drill, verdict=verdict, sha=sha, tests=output
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "body.md"
            path.write_text(body)
            url = self._must(
                ["gh", "pr", "create", "--repo", self.repo, "--base", self.base, "--head", branch,
                 "--title", message, "--body-file", str(path), "--label", label],
                work,
            )  # fmt: skip
        return Filed("pr", url, branch)
