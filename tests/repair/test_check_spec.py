from pathlib import Path

from typer.testing import CliRunner

from pothole.cli import app
from pothole.repair.validate import reproduces
from pothole.socrata import batches

ROOT = Path(__file__).parents[2]
RUNNER = CliRunner()
PINNED = ROOT / "fixtures/batches/spec.yaml"


def test_check_spec_passes_on_the_committed_batches() -> None:
    res = RUNNER.invoke(
        app,
        [
            "check-spec",
            "--spec",
            str(PINNED),
            "--batches",
            str(ROOT / "fixtures/batches"),
        ],  # fmt: skip
    )
    assert res.exit_code == 0 and res.output.count(": ok") == 6


def test_check_spec_exits_non_zero_on_a_failing_batch(tmp_path: Path, failing) -> None:
    batches.dump(tmp_path / "2026-11-07.jsonl.gz", failing.rows)
    res = RUNNER.invoke(app, ["check-spec", "--spec", str(PINNED), "--batches", str(tmp_path)])
    assert res.exit_code == 1 and "FAIL" in res.output and "agency" in res.output


def test_a_case_that_does_not_match_does_not_reproduce(spec, outcome_case) -> None:
    assert reproduces(spec, outcome_case)
    bad = type(outcome_case)(outcome_case.rows, [{**outcome_case.expected[0], "agency": "zz"}])
    assert not reproduces(spec, bad)
