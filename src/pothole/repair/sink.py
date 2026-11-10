"""Where a decision goes: files on disk (LocalSink) or a pull request / issue (GitHubSink).

Nothing here touches `main`: a patch lives on a `repair/<date>-<n>` or `drill/<date>-<n>` branch,
and the pull request is opened for a person to merge.
"""

import os
import secrets
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
from pothole.repair.validate import case_day
from pothole.socrata import batches
from pothole.spec import ops
from pothole.spec.checks import CheckResult
from pothole.spec.model import PipelineSpec

Runner = Callable[..., "subprocess.CompletedProcess[str]"]
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
PYTEST = ["uv", "run", "pytest", "-q"]  # the whole suite, regression cases included


def check_spec_command(batch_dir: str) -> list[str]:
    return [
        "uv",
        "run",
        "pothole",
        "check-spec",
        "--spec",
        render.SPEC_PATH,
        "--batches",
        batch_dir,
    ]


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


def _name(drill: bool, day: date, suffix: str) -> str:
    return f"{'drill' if drill else 'repair'}/{day.isoformat()}-{suffix}"


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
        branch = _name(drill, day, secrets.token_hex(3))
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

    def _ensure_labels(self, names: Sequence[str]) -> None:
        for name in names:
            self._run(["gh", "label", "create", name, "--repo", self.repo, "--force"])

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
                self._ensure_labels(render.LABELS_DRILL)
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
        self._must(["git", "fetch", "origin", self.base])
        branch = _name(drill, day, secrets.token_hex(3))
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
        ident = branch.replace("/", "-")
        (work / "pipelines").mkdir(exist_ok=True)
        (work / "tests" / "regression").mkdir(parents=True, exist_ok=True)
        (work / render.SPEC_PATH).write_text(ops.apply(spec, outcome.ops).to_yaml())
        case = work / "tests" / "regression" / f"{ident}.json"
        case.write_text(render.regression_file(outcome.regression))
        # the case's rows as a batch of their own, so check-spec can run the patched spec on them
        rows_dir = work / "tests" / "regression" / "batches" / ident
        rows_dir.mkdir(parents=True)
        when = case_day(ops.apply(spec, outcome.ops), outcome.regression.rows).isoformat()
        batches.dump(rows_dir / f"{when}.jsonl.gz", outcome.regression.rows)
        paths = [render.SPEC_PATH, *(str(p.relative_to(work)) for p in (case, rows_dir))]
        self._must(["git", "add", *paths], work)
        who = ["-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}"]
        message = render.pr_title(outcome, drill)
        self._must(["git", *who, "commit", "-m", message], work)
        sha = self._must(["git", "rev-parse", "HEAD"], work)
        output = ""
        gates = [PYTEST, check_spec_command(str(rows_dir.relative_to(work)))]
        for gate in gates:
            done = self._run(gate, work)
            output += f"$ {' '.join(gate)}\n{done.stdout}{done.stderr}\n"
            if done.returncode != 0:
                reason = (
                    f"The patch failed its own tests on {branch} ({sha[:10]}): {outcome.reason}"
                )
                return self.open_issue(reason, before, day=day, drill=drill, output=output)
        wanted = render.labels(drill)
        try:
            self._must(["git", "push", "origin", f"{branch}:refs/heads/{branch}"], work)
            self._ensure_labels(wanted)
            body = render.pr_body(
                outcome, before, after, drill=drill, verdict=verdict, sha=sha, tests=output
            )
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "body.md"
                path.write_text(body)
                create = ["gh", "pr", "create", "--repo", self.repo, "--base", self.base]
                create += ["--head", branch, "--title", message, "--body-file", str(path)]
                for name in wanted:
                    create += ["--label", name]
                url = self._must(create, work)
        except RuntimeError as exc:
            self._run(["git", "push", "origin", "--delete", branch], work)  # no PR, no stray branch
            reason = f"Could not open the pull request for {branch}: {exc}. {outcome.reason}"
            return self.open_issue(reason, before, day=day, drill=drill, output=output)
        return Filed("pr", url, branch)
