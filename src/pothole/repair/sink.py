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

import yaml

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


def check_spec_command(spec_path: str, batch_dir: str) -> list[str]:
    return [
        "uv",
        "run",
        "pothole",
        "check-spec",
        "--spec",
        spec_path,
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


@dataclass(frozen=True)
class Written:
    paths: list[str]  # to commit, relative to the root
    spec_path: str  # the patched spec, relative to the root
    rows_dir: Path  # the case's rows as a dated batch


def write_patch_files(
    root: Path,
    branch: str,
    spec: PipelineSpec,
    outcome: RepairOutcome,
    *,
    drill: bool,
    verdict: str | None,
) -> Written:
    """Write the patched spec, the regression case and the case's rows as a dated batch of their
    own (so check-spec can run the patched spec on them). A real repair changes the live spec and
    adds a case under tests/regression; a drill writes only under drills/runs/<branch>/, the
    patched pinned spec, the case and the judge's verdict, and never touches the live spec."""
    assert outcome.regression is not None
    ident = branch.replace("/", "-")
    patched = ops.apply(spec, outcome.ops)
    if drill:
        folder = root / render.DRILL_DIR / ident
        spec_file, case_file, rows_dir = (
            folder / "spec.yaml",
            folder / "case.json",
            folder / "batches",
        )
        committed = [folder]
    else:
        spec_file = root / render.SPEC_PATH
        case_file = root / "tests" / "regression" / f"{ident}.json"
        rows_dir = root / "tests" / "regression" / "batches" / ident
        committed = [spec_file, case_file, rows_dir]
    for directory in (spec_file.parent, case_file.parent):
        directory.mkdir(parents=True, exist_ok=True)
    rows_dir.mkdir(parents=True)
    if drill:
        (spec_file.parent / "verdict.txt").write_text((verdict or "") + "\n")
    spec_file.write_text(patched.to_yaml())
    case_file.write_text(render.regression_file(outcome.regression, patched))
    day = case_day(patched, outcome.regression.rows).isoformat()
    batches.dump(rows_dir / f"{day}.jsonl.gz", outcome.regression.rows)
    return Written(
        [str(p.relative_to(root)) for p in committed],
        str(spec_file.relative_to(root)),
        rows_dir,
    )


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
        root.mkdir(parents=True)
        write_patch_files(root, branch, spec, outcome, drill=drill, verdict=verdict)
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
        self,
        args: Sequence[str],
        cwd: Path | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> "subprocess.CompletedProcess[str]":
        return self.runner(
            list(args), cwd=cwd or self.repo_dir, env={**self.env, **(extra_env or {})}
        )

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

    def _changed_since_diagnosis(self, work: Path, diagnosed: PipelineSpec) -> str | None:
        """Why a real patch cannot be applied to the base as it stands, or None when it can: the
        patch is applied only to the spec the agent diagnosed, identified by its digest."""
        try:
            base = PipelineSpec.load(work / render.SPEC_PATH)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            return (
                f"The spec on {self.base} cannot be read ({type(exc).__name__}); no patch applied."
            )
        if render.spec_digest(base) != render.spec_digest(diagnosed):
            return f"The spec changed since diagnosis: {self.base} is not the spec the agent saw."
        return None

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
        if not drill:
            changed = self._changed_since_diagnosis(work, spec)
            if changed:
                return self.open_issue(changed, before, day=day, drill=drill)
            spec = PipelineSpec.load(work / render.SPEC_PATH)  # the very spec that was diagnosed
        written = write_patch_files(work, branch, spec, outcome, drill=drill, verdict=verdict)
        self._must(["git", "add", *written.paths], work)
        who = ["-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}"]
        message = render.pr_title(outcome, drill)
        self._must(["git", *who, "commit", "-m", message], work)
        sha = self._must(["git", "rev-parse", "HEAD"], work)
        output = ""
        gates = [
            PYTEST,
            check_spec_command(written.spec_path, str(written.rows_dir.relative_to(work))),
        ]
        for gate in gates:
            # the branch's own source, whatever else is installed in the environment
            done = self._run(gate, work, {"PYTHONPATH": str(work / "src")})
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
